import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, Member
from member_accounts.delivery import LoginDeliveryUnavailable, send_login_code
from member_accounts.models import MemberAccount, MemberInvitation, MemberLoginChallenge
from members.models import MemberDeviceToken


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _keyed_digest(value):
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        value.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _normalize_phone(phone):
    return ''.join((phone or '').strip().split())


def _phone_hash(phone):
    return _keyed_digest(f'phone:{_normalize_phone(phone).lower()}')


def _login_code_hash(challenge_uuid, code):
    return _keyed_digest(f'login:{challenge_uuid}:{code}')


def _invitation_age_seconds():
    return int(getattr(
        settings,
        'MEMBER_INVITATION_TOKEN_AGE',
        getattr(settings, 'MEMBER_ACTIVATION_TOKEN_AGE', 60 * 60 * 24 * 7),
    ))


def ensure_member_account(member):
    account, _ = MemberAccount.objects.get_or_create(member=member)
    return account


def issue_trusted_device(member, device_label=''):
    """Issue the same revocable browser credential used by existing member recognition."""
    now = timezone.now()
    secret = secrets.token_urlsafe(32)
    device = MemberDeviceToken.objects.create(
        member=member,
        token_hash=_digest(secret),
        device_label=(device_label or '')[:120],
        expires_at=now + timedelta(seconds=settings.MEMBER_DEVICE_COOKIE_AGE),
    )
    return device, f'{device.uuid}.{secret}'


def create_invitation(*, member=None, invited_phone='', invited_name='', created_by=None,
                      purpose=MemberInvitation.Purpose.ACCOUNT_CLAIM, expires_at=None):
    invited_phone = (invited_phone or '').strip()
    invited_name = (invited_name or '').strip()
    if member is None and not invited_phone:
        raise ValidationError('رقم الهاتف مطلوب عند إنشاء دعوة لشخص غير مسجل.')
    if member is not None:
        ensure_member_account(member)
        invited_phone = invited_phone or member.phone
        invited_name = invited_name or member.name_ar
        if purpose == MemberInvitation.Purpose.ACCOUNT_CLAIM:
            MemberInvitation.objects.filter(
                target_member=member,
                purpose=purpose,
                claimed_at__isnull=True,
                revoked_at__isnull=True,
            ).update(revoked_at=timezone.now())
    raw = secrets.token_urlsafe(32)
    invitation = MemberInvitation.objects.create(
        target_member=member,
        invited_phone=invited_phone,
        invited_name=invited_name,
        purpose=purpose,
        token_hash=_digest(raw),
        expires_at=expires_at or timezone.now() + timedelta(seconds=_invitation_age_seconds()),
        created_by=created_by,
    )
    ActivityLog.objects.create(
        actor=created_by,
        action='member_account.invitation_created',
        details={
            'invitation_uuid': str(invitation.uuid),
            'member_public_code': str(member.public_code) if member else '',
            'purpose': purpose,
        },
    )
    return invitation, raw


def validate_invitation(raw, at=None):
    now = at or timezone.now()
    digest = _digest(raw)
    invitation = MemberInvitation.objects.select_related('target_member').filter(token_hash=digest).first()
    if (
        not invitation
        or not hmac.compare_digest(invitation.token_hash, digest)
        or invitation.claimed_at
        or invitation.revoked_at
        or invitation.expires_at <= now
    ):
        return None
    return invitation


@dataclass(frozen=True)
class ClaimResult:
    account: MemberAccount
    member: Member
    device: MemberDeviceToken
    cookie_value: str


@transaction.atomic
def claim_invitation(raw, *, name='', device_label=''):
    now = timezone.now()
    digest = _digest(raw)
    invitation = (
        MemberInvitation.objects.select_for_update(of=('self',))
        .select_related('target_member')
        .filter(token_hash=digest)
        .first()
    )
    if (
        not invitation
        or not hmac.compare_digest(invitation.token_hash, digest)
        or invitation.claimed_at
        or invitation.revoked_at
        or invitation.expires_at <= now
    ):
        raise ValidationError('الدعوة غير صالحة أو مستخدمة أو منتهية الصلاحية.')

    member = invitation.target_member
    if member is None:
        phone = invitation.invited_phone.strip()
        member = Member.objects.filter(phone__iexact=phone).first()
        if member is None:
            final_name = (name or invitation.invited_name).strip()
            if not final_name:
                raise ValidationError('الاسم مطلوب لإكمال إنشاء الحساب.')
            member = Member.objects.create(name_ar=final_name, phone=phone)
        invitation.target_member = member

    account = ensure_member_account(member)
    if account.status == MemberAccount.Status.LOCKED:
        raise ValidationError('هذا الحساب موقوف. راجع إدارة هَب.')
    account.mark_claimed(now)

    invitation.claimed_at = now
    invitation.claimed_member = member
    invitation.save(update_fields=['target_member', 'claimed_at', 'claimed_member'])

    device, cookie_value = issue_trusted_device(member, device_label)
    ActivityLog.objects.create(
        actor=invitation.created_by,
        action='member_account.invitation_claimed',
        details={
            'invitation_uuid': str(invitation.uuid),
            'member_public_code': str(member.public_code),
            'device_uuid': str(device.uuid),
        },
    )
    return ClaimResult(account=account, member=member, device=device, cookie_value=cookie_value)


@dataclass(frozen=True)
class LoginRequestResult:
    challenge: MemberLoginChallenge
    delivered: bool
    throttled: bool


def request_login_challenge(phone, *, ip='', user_agent='', next_path=''):
    """Create a phone-login challenge without exposing whether the phone exists."""
    normalized = _normalize_phone(phone)
    if not normalized:
        raise ValidationError('رقم الهاتف مطلوب.')

    now = timezone.now()
    phone_digest = _phone_hash(normalized)
    resend_seconds = int(getattr(settings, 'MEMBER_LOGIN_RESEND_SECONDS', 60))
    window_seconds = int(getattr(settings, 'MEMBER_LOGIN_RATE_WINDOW_SECONDS', 15 * 60))
    max_requests = int(getattr(settings, 'MEMBER_LOGIN_MAX_REQUESTS_PER_WINDOW', 5))
    recent_qs = MemberLoginChallenge.objects.filter(
        phone_hash=phone_digest,
        created_at__gte=now - timedelta(seconds=window_seconds),
    ).order_by('-created_at')
    recent = recent_qs.first()
    if recent and recent.created_at > now - timedelta(seconds=resend_seconds):
        return LoginRequestResult(recent, delivered=False, throttled=True)
    if recent_qs.count() >= max_requests and recent:
        return LoginRequestResult(recent, delivered=False, throttled=True)

    member = Member.objects.filter(phone__iexact=normalized).first()
    if member is not None:
        account = ensure_member_account(member)
        if account.status == MemberAccount.Status.LOCKED:
            member = None

    code = f'{secrets.randbelow(1_000_000):06d}'
    challenge = MemberLoginChallenge(
        member=member,
        phone_hash=phone_digest,
        expires_at=now + timedelta(seconds=int(getattr(settings, 'MEMBER_LOGIN_CODE_AGE', 10 * 60))),
        requested_ip_hash=_keyed_digest(f'ip:{ip}') if ip else '',
        next_path=(next_path or '')[:500],
        user_agent=(user_agent or '')[:160],
    )
    challenge.code_hash = _login_code_hash(challenge.uuid, code)
    challenge.delivery_status = (
        MemberLoginChallenge.DeliveryStatus.PENDING
        if member is not None else MemberLoginChallenge.DeliveryStatus.SKIPPED
    )
    challenge.save()

    delivered = False
    if member is not None:
        try:
            send_login_code(normalized, code, challenge.uuid)
        except LoginDeliveryUnavailable:
            challenge.delivery_status = MemberLoginChallenge.DeliveryStatus.FAILED
        else:
            challenge.delivery_status = MemberLoginChallenge.DeliveryStatus.SENT
            delivered = True
        challenge.save(update_fields=['delivery_status'])
        ActivityLog.objects.create(
            action='member_account.login_code_requested',
            details={
                'member_public_code': str(member.public_code),
                'challenge_uuid': str(challenge.uuid),
                'delivery_status': challenge.delivery_status,
            },
        )

    return LoginRequestResult(challenge, delivered=delivered, throttled=False)


def verify_login_challenge(challenge_uuid, code, *, device_label=''):
    """Verify one OTP and issue a long-lived revocable trusted-device credential.

    Failed-attempt writes must commit before ValidationError is raised; otherwise
    the enclosing atomic block would roll the counter back on every bad code.
    """
    now = timezone.now()
    validation_error = None
    result = None

    with transaction.atomic():
        # Lock only the challenge row. member is nullable, so PostgreSQL cannot
        # apply FOR UPDATE to the LEFT OUTER JOIN produced by select_related.
        challenge = (
            MemberLoginChallenge.objects.select_for_update(of=('self',))
            .select_related('member')
            .filter(uuid=challenge_uuid)
            .first()
        )
        if (
            not challenge
            or challenge.consumed_at
            or challenge.expires_at <= now
            or challenge.attempts >= challenge.max_attempts
        ):
            validation_error = ValidationError('رمز التحقق غير صالح أو منتهي الصلاحية.')
        else:
            challenge.attempts += 1
            submitted = (code or '').strip()
            valid = bool(challenge.member) and hmac.compare_digest(
                challenge.code_hash,
                _login_code_hash(challenge.uuid, submitted),
            )
            if not valid:
                fields = ['attempts']
                if challenge.attempts >= challenge.max_attempts:
                    challenge.consumed_at = now
                    fields.append('consumed_at')
                challenge.save(update_fields=fields)
                validation_error = ValidationError('رمز التحقق غير صالح أو منتهي الصلاحية.')
            else:
                account = ensure_member_account(challenge.member)
                if account.status == MemberAccount.Status.LOCKED:
                    challenge.consumed_at = now
                    challenge.save(update_fields=['attempts', 'consumed_at'])
                    validation_error = ValidationError('تعذر تسجيل الدخول إلى هذا الحساب.')
                else:
                    account.mark_claimed(now)
                    account.phone_verified_at = now
                    account.save(update_fields=['phone_verified_at', 'updated_at'])
                    challenge.consumed_at = now
                    challenge.save(update_fields=['attempts', 'consumed_at'])

                    device, cookie_value = issue_trusted_device(challenge.member, device_label)
                    ActivityLog.objects.create(
                        action='member_account.login_verified',
                        details={
                            'member_public_code': str(challenge.member.public_code),
                            'challenge_uuid': str(challenge.uuid),
                            'device_uuid': str(device.uuid),
                        },
                    )
                    result = ClaimResult(
                        account=account,
                        member=challenge.member,
                        device=device,
                        cookie_value=cookie_value,
                    )

    if validation_error is not None:
        raise validation_error
    return result
