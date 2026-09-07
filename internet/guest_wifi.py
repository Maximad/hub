"""Complimentary captive-portal Internet for in-venue visitors.

The Wi-Fi SSID may be open, but Internet authorization is not. Anonymous visitors
prove venue presence with a rotating code and receive a bounded package-less
InternetSession. Permanent member identity remains optional and membership benefits
remain authoritative elsewhere; this module never creates a member account.
"""
import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, HubVisit, InternetSession
from core.services.visit_internet import _metered_network_provider, prepare_visit_metered_session_network
from core.services.visit_internet_devices import active_browser_session, bind_session_to_credential
from internet.models import GuestWifiCodeAttempt, GuestWifiGrant, GuestWifiPolicy
from internet.session_network_backends import NOT_PROVISIONED
from internet.session_network_operations import enqueue_session_network_operation
from internet.models import InternetSessionNetworkOperation


CODE_DIGITS = 4
ATTEMPT_WINDOW = timedelta(minutes=10)
ATTEMPT_LIMIT = 5
BLOCK_TIME = timedelta(minutes=15)
PREVIOUS_CODE_GRACE = timedelta(minutes=10)
DEFAULT_PROFILE_CODE = 'basic'


def get_guest_wifi_policy():
    """Return the singleton policy without creating database state on a public GET."""
    return (
        GuestWifiPolicy.objects.select_related('bandwidth_profile')
        .filter(key='default')
        .first()
        or GuestWifiPolicy(key='default')
    )


def guest_wifi_policy_error(policy):
    if not policy.enabled:
        return 'الإنترنت المجاني للزوار غير متاح حالياً.'
    if int(policy.session_minutes or 0) <= 0:
        return 'مدة إنترنت الزوار غير مضبوطة.'
    if int(policy.max_sessions_per_day or 0) <= 0:
        return 'حد جلسات إنترنت الزوار غير مضبوط.'
    if int(policy.code_rotation_minutes or 0) <= 0:
        return 'دورة رمز إنترنت الزوار غير مضبوطة.'
    return None


def venue_code_slot(policy, at=None):
    at = at or timezone.now()
    seconds = max(int(policy.code_rotation_minutes or 0), 1) * 60
    return int(at.timestamp()) // seconds


def venue_code_for_slot(policy, slot):
    digest = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        f'hub-guest-wifi:{policy.key}:{slot}'.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    value = int.from_bytes(digest[:8], 'big') % (10 ** CODE_DIGITS)
    return str(value).zfill(CODE_DIGITS)


def current_venue_code(policy=None, at=None):
    policy = policy or get_guest_wifi_policy()
    return venue_code_for_slot(policy, venue_code_slot(policy, at))


def _code_matches(policy, raw_code, at=None):
    at = at or timezone.now()
    code = ''.join(ch for ch in (raw_code or '').strip() if ch.isdigit())
    slot = venue_code_slot(policy, at)
    if hmac.compare_digest(code, venue_code_for_slot(policy, slot)):
        return slot

    # Keep a short overlap so a guest is not rejected because the code rotated
    # while the captive page was already open on their phone.
    rotation = timedelta(minutes=max(int(policy.code_rotation_minutes or 0), 1))
    slot_started_at = timezone.datetime.fromtimestamp(
        slot * int(rotation.total_seconds()), tz=at.tzinfo
    )
    if at - slot_started_at <= PREVIOUS_CODE_GRACE:
        previous = slot - 1
        if hmac.compare_digest(code, venue_code_for_slot(policy, previous)):
            return previous
    return None


def _request_fingerprint(request):
    # The reverse proxy is expected to set X-Real-IP. We deliberately avoid
    # storing the raw address or user-agent in the abuse-control table.
    client_ip = (request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR') or '').strip()
    user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:255]
    payload = f'{client_ip}|{user_agent}'.encode('utf-8')
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), payload, hashlib.sha256).hexdigest()


@transaction.atomic
def validate_venue_code(request, policy, raw_code, *, at=None):
    """Validate the rotating code with persistent, privacy-preserving throttling."""
    at = at or timezone.now()
    fingerprint = _request_fingerprint(request)
    bucket = GuestWifiCodeAttempt.objects.select_for_update().filter(
        fingerprint_hash=fingerprint
    ).first()
    if bucket and bucket.blocked_until and bucket.blocked_until > at:
        raise ValidationError('محاولات كثيرة. انتظر قليلاً ثم أعد المحاولة.')

    slot = _code_matches(policy, raw_code, at)
    if slot is not None:
        if bucket:
            bucket.delete()
        return slot

    if bucket is None:
        bucket = GuestWifiCodeAttempt(
            fingerprint_hash=fingerprint,
            window_started_at=at,
            attempt_count=0,
        )
    elif at - bucket.window_started_at >= ATTEMPT_WINDOW:
        bucket.window_started_at = at
        bucket.attempt_count = 0
        bucket.blocked_until = None

    bucket.attempt_count += 1
    if bucket.attempt_count >= ATTEMPT_LIMIT:
        bucket.blocked_until = at + BLOCK_TIME
    bucket.save()
    if bucket.blocked_until and bucket.blocked_until > at:
        raise ValidationError('محاولات كثيرة. انتظر 15 دقيقة ثم أعد المحاولة.')
    raise ValidationError('رمز المكان غير صحيح.')


def _business_date(at):
    return timezone.localtime(at).date()


def _member_bypasses_code(policy, member_context):
    return bool(
        policy.member_bypass_venue_code
        and member_context
        and member_context.subscription is not None
    )


def guest_wifi_code_required(policy, member_context=None):
    return bool(policy.require_venue_code and not _member_bypasses_code(policy, member_context))


def guest_wifi_grants_used(credential, policy=None, at=None):
    if not credential:
        return 0
    at = at or timezone.now()
    return GuestWifiGrant.objects.filter(
        credential=credential,
        business_date=_business_date(at),
    ).count()


def guest_wifi_grants_remaining(credential, policy=None, at=None):
    policy = policy or get_guest_wifi_policy()
    return max(int(policy.max_sessions_per_day or 0) - guest_wifi_grants_used(credential, policy, at), 0)


@transaction.atomic
def start_guest_wifi_session(*, request, visit, credential, member_context=None,
                             venue_code='', actor=None, at=None):
    """Start or reuse this browser's bounded complimentary visitor session."""
    at = at or timezone.now()
    policy = get_guest_wifi_policy()
    error = guest_wifi_policy_error(policy)
    if error:
        raise ValidationError(error)

    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    if not credential or credential.visit_id != visit.pk or visit.status != HubVisit.Status.OPEN:
        raise ValidationError('الجلسة مغلقة.')

    active = active_browser_session(credential)
    if active:
        if GuestWifiGrant.objects.filter(session=active, credential=credential).exists():
            return active, False
        raise ValidationError('لديك جلسة إنترنت فعالة على هذا الجهاز. أنهِها قبل بدء جلسة أخرى.')

    member = member_context.member if member_context else None
    code_slot = None
    if guest_wifi_code_required(policy, member_context):
        code_slot = validate_venue_code(request, policy, venue_code, at=at)

    business_date = _business_date(at)
    used = GuestWifiGrant.objects.filter(
        credential=credential,
        business_date=business_date,
    ).count()
    if used >= int(policy.max_sessions_per_day or 0):
        raise ValidationError('استخدم هذا الجهاز الحد المجاني المتاح لليوم.')

    network_provider = _metered_network_provider()
    profile_code = (
        policy.bandwidth_profile.code
        if policy.bandwidth_profile_id
        else DEFAULT_PROFILE_CODE
    )
    session = InternetSession.objects.create(
        session_type=InternetSession.SessionType.INTERNET,
        member=member,
        visit=visit,
        package=None,
        entitlement=None,
        billing_mode=InternetSession.BillingMode.FREE,
        started_at=at,
        start_time=at,
        authorized_minutes=int(policy.session_minutes),
        authorized_until=None,
        rate_per_hour_syp=0,
        minimum_minutes=0,
        free_grace_minutes=0,
        rounding_increment_minutes=1,
        minimum_charge_syp=0,
        notes='إنترنت زوار مجاني — بوابة هَبّ',
        status=InternetSession.Status.ACTIVE,
        started_by=actor,
        bandwidth_profile=(
            profile_code
            if network_provider == InternetSession.NetworkProvider.MIKROTIK
            else ''
        ),
        network_provider=network_provider,
        network_status=NOT_PROVISIONED,
    )
    bind_session_to_credential(session, credential)
    GuestWifiGrant.objects.create(
        credential=credential,
        session=session,
        business_date=business_date,
        code_slot=code_slot,
    )
    enqueue_session_network_operation(
        session,
        InternetSessionNetworkOperation.Operation.PROVISION,
        reason='guest Wi-Fi captive start',
        process_after_commit=False,
    )
    HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=at)
    ActivityLog.objects.create(actor=actor, action='guest_wifi.session_requested', details={
        'visit_id': visit.pk,
        'session_id': session.pk,
        'member_id': member.pk if member else None,
        'session_minutes': int(policy.session_minutes),
        'network_provider': network_provider,
        'bandwidth_profile': session.bandwidth_profile,
    })
    return session, True


def prepare_guest_wifi_session_network(session):
    """Provision immediately while retaining the durable retry operation on failure."""
    return prepare_visit_metered_session_network(session)
