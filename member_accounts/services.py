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
from member_accounts.models import MemberAccount, MemberInvitation
from members.models import MemberDeviceToken


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


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
        # A newly generated account invitation replaces older unclaimed links for the same member.
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
        MemberInvitation.objects.select_for_update()
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
