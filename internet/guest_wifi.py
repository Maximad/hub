"""Complimentary/basic captive-portal Internet for Hub visitors.

The open SSID is only transport. This module owns the bounded slow/basic allowance:
first daily venue-verified access, order-earned extensions and a daily complimentary
ceiling. Paid fast Internet and entitlements remain in the existing commercial
Internet engine and are deliberately not limited by this policy.
"""
import hashlib
import hmac
import math
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, HubVisit, InternetSession, Order
from core.services.internet_access import end_usage_session
from core.services.visit_internet import _metered_network_provider, prepare_visit_metered_session_network
from core.services.visit_internet_devices import active_browser_session, bind_session_to_credential
from internet.models import (
    GuestWifiCodeAttempt,
    GuestWifiDailyAllowance,
    GuestWifiGrant,
    GuestWifiOrderBonus,
    GuestWifiPolicy,
    InternetSessionNetworkOperation,
    InternetSessionNetworkState,
)
from internet.session_network_backends import NOT_PROVISIONED
from internet.session_network_operations import enqueue_session_network_operation


CODE_DIGITS = 4
ATTEMPT_WINDOW = timedelta(minutes=10)
ATTEMPT_LIMIT = 5
BLOCK_TIME = timedelta(minutes=15)
PREVIOUS_CODE_GRACE = timedelta(minutes=10)
DEFAULT_PROFILE_CODE = 'basic'
ELIGIBLE_ORDER_STATUSES = {
    Order.Status.ACCEPTED,
    Order.Status.PREPARING,
    Order.Status.READY,
    Order.Status.SERVED,
}


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
        return 'الإنترنت الأساسي غير متاح حالياً.'
    if int(policy.session_minutes or 0) <= 0:
        return 'الرصيد الأولي للإنترنت الأساسي غير مضبوط.'
    if int(policy.daily_complimentary_minutes or 0) <= 0:
        return 'السقف المجاني اليومي غير مضبوط.'
    if int(policy.daily_complimentary_minutes or 0) < int(policy.session_minutes or 0):
        return 'السقف المجاني اليومي يجب أن يساوي أو يتجاوز الرصيد الأولي.'
    if policy.order_bonus_enabled and int(policy.order_bonus_minutes or 0) <= 0:
        return 'دقائق مكافأة الطلب غير مضبوطة.'
    if policy.require_venue_code and int(policy.code_rotation_minutes or 0) <= 0:
        return 'دورة رمز المكان غير مضبوطة.'
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
    # The reverse proxy is expected to set X-Real-IP. Raw IP/user-agent are never stored.
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


def _allowance(credential, *, at=None, create=False, lock=False):
    if not credential:
        return None
    at = at or timezone.now()
    queryset = GuestWifiDailyAllowance.objects
    if lock:
        queryset = queryset.select_for_update()
    kwargs = {
        'credential': credential,
        'business_date': _business_date(at),
    }
    if create:
        allowance, _ = queryset.get_or_create(**kwargs)
        return allowance
    return queryset.filter(**kwargs).first()


def guest_wifi_code_required(policy, member_context=None, credential=None, at=None):
    """Only the first basic activation of the day needs proof of venue presence."""
    if not policy.require_venue_code or _member_bypasses_code(policy, member_context):
        return False
    allowance = _allowance(credential, at=at)
    return not allowance or int(allowance.initial_minutes_granted or 0) <= 0


def _network_activation(session):
    return InternetSessionNetworkState.objects.filter(session_id=session.pk).first()


def _live_basic_seconds(allowance, at=None):
    """Return not-yet-accounted seconds used by the currently active basic session."""
    if not allowance:
        return 0
    at = at or timezone.now()
    grant = (
        GuestWifiGrant.objects.filter(
            allowance=allowance,
            usage_accounted_at__isnull=True,
            session__status=InternetSession.Status.ACTIVE,
        )
        .select_related('session')
        .order_by('-created_at')
        .first()
    )
    if not grant:
        return 0
    state = _network_activation(grant.session)
    if not state or not state.network_activated_at:
        return 0
    end = at
    if grant.session.authorized_until and grant.session.authorized_until < end:
        end = grant.session.authorized_until
    return max(int((end - state.network_activated_at).total_seconds()), 0)


def guest_wifi_total_granted_minutes(credential, policy=None, at=None):
    allowance = _allowance(credential, at=at)
    return int(allowance.total_granted_minutes) if allowance else 0


def guest_wifi_remaining_seconds(credential, policy=None, at=None):
    policy = policy or get_guest_wifi_policy()
    allowance = _allowance(credential, at=at)
    if not allowance:
        return 0
    cap_seconds = int(policy.daily_complimentary_minutes or 0) * 60
    granted_seconds = min(int(allowance.total_granted_minutes) * 60, cap_seconds)
    used_seconds = int(allowance.consumed_seconds or 0) + _live_basic_seconds(allowance, at)
    return max(granted_seconds - used_seconds, 0)


def guest_wifi_daily_minutes_remaining(credential, policy=None, at=None):
    seconds = guest_wifi_remaining_seconds(credential, policy, at)
    return int(math.ceil(seconds / 60)) if seconds else 0


def guest_wifi_grants_used(credential, policy=None, at=None):
    """Legacy compatibility metric: count basic session rows for the current day."""
    if not credential:
        return 0
    at = at or timezone.now()
    return GuestWifiGrant.objects.filter(
        credential=credential,
        business_date=_business_date(at),
    ).count()


def guest_wifi_grants_remaining(credential, policy=None, at=None):
    """Legacy compatibility helper; policy now limits minutes rather than session count."""
    policy = policy or get_guest_wifi_policy()
    allowance = _allowance(credential, at=at)
    if not allowance or int(allowance.initial_minutes_granted or 0) <= 0:
        return 1
    return 1 if guest_wifi_remaining_seconds(credential, policy, at) > 0 else 0


def pending_order_bonus_minutes(credential, at=None):
    if not credential:
        return 0
    allowance = _allowance(credential, at=at)
    if not allowance:
        return 0
    return sum(
        bonus.minutes
        for bonus in allowance.order_bonuses.filter(
            revoked_at__isnull=True,
            applied_at__isnull=True,
        )
    )


def _grant_initial_allowance(request, allowance, policy, member_context, *, at):
    if int(allowance.initial_minutes_granted or 0) > 0:
        return None
    code_slot = None
    if guest_wifi_code_required(policy, member_context, allowance.credential, at):
        code_slot = validate_venue_code(request, policy, request.POST.get('venue_code', ''), at=at)
    available = max(
        int(policy.daily_complimentary_minutes or 0) - int(allowance.total_granted_minutes),
        0,
    )
    grant = min(int(policy.session_minutes or 0), available)
    if grant <= 0:
        raise ValidationError('استخدم هذا الجهاز الحد المجاني المتاح لليوم.')
    allowance.initial_minutes_granted = grant
    allowance.save(update_fields=['initial_minutes_granted', 'updated_at'])
    return code_slot


def _mark_pending_bonuses_applied(allowance, session, at):
    return allowance.order_bonuses.filter(
        revoked_at__isnull=True,
        applied_at__isnull=True,
    ).update(applied_session=session, applied_at=at, updated_at=at)


@transaction.atomic
def start_guest_wifi_session(*, request, visit, credential, member_context=None,
                             venue_code='', actor=None, at=None):
    """Start/reuse this browser's slow basic access from its daily allowance."""
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
            if guest_wifi_remaining_seconds(credential, policy, at) <= 0:
                raise ValidationError('انتهى رصيد الإنترنت الأساسي المتاح لهذا الجهاز اليوم.')
            return active, False
        raise ValidationError('لديك إنترنت سريع فعال على هذا الجهاز حالياً.')

    allowance = _allowance(credential, at=at, create=True, lock=True)
    code_slot = _grant_initial_allowance(request, allowance, policy, member_context, at=at)
    remaining_seconds = guest_wifi_remaining_seconds(credential, policy, at)
    if remaining_seconds <= 0:
        raise ValidationError('استخدم هذا الجهاز الحد المجاني المتاح لليوم.')

    member = member_context.member if member_context else None
    network_provider = _metered_network_provider()
    profile_code = (
        policy.bandwidth_profile.code
        if policy.bandwidth_profile_id
        else DEFAULT_PROFILE_CODE
    )
    authorized_minutes = max(int(math.ceil(remaining_seconds / 60)), 1)
    session = InternetSession.objects.create(
        session_type=InternetSession.SessionType.INTERNET,
        member=member,
        visit=visit,
        package=None,
        entitlement=None,
        billing_mode=InternetSession.BillingMode.FREE,
        started_at=at,
        start_time=at,
        authorized_minutes=authorized_minutes,
        authorized_until=None,
        rate_per_hour_syp=0,
        minimum_minutes=0,
        free_grace_minutes=0,
        rounding_increment_minutes=1,
        minimum_charge_syp=0,
        notes='إنترنت أساسي مجاني — بوابة هَبّ',
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
        allowance=allowance,
        business_date=allowance.business_date,
        code_slot=code_slot,
    )
    _mark_pending_bonuses_applied(allowance, session, at)
    enqueue_session_network_operation(
        session,
        InternetSessionNetworkOperation.Operation.PROVISION,
        reason='basic Wi-Fi captive start',
        process_after_commit=False,
    )
    HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=at)
    ActivityLog.objects.create(actor=actor, action='guest_wifi.session_requested', details={
        'visit_id': visit.pk,
        'session_id': session.pk,
        'member_id': member.pk if member else None,
        'authorized_minutes': authorized_minutes,
        'daily_cap_minutes': int(policy.daily_complimentary_minutes or 0),
        'network_provider': network_provider,
        'bandwidth_profile': session.bandwidth_profile,
    })
    return session, True


def prepare_guest_wifi_session_network(session):
    """Provision immediately while retaining the durable retry operation on failure."""
    return prepare_visit_metered_session_network(session)


@transaction.atomic
def account_guest_wifi_session_usage(session, *, at=None):
    """Persist actual basic-Internet usage exactly once after a session ends."""
    at = at or timezone.now()
    grant = (
        GuestWifiGrant.objects.select_for_update()
        .filter(session_id=session.pk)
        .first()
    )
    if not grant or grant.usage_accounted_at or not grant.allowance_id:
        return 0
    state = _network_activation(session)
    seconds = 0
    if state and state.network_activated_at:
        end = session.end_time or session.ended_at or at
        if session.authorized_until and session.authorized_until < end:
            end = session.authorized_until
        seconds = max(int((end - state.network_activated_at).total_seconds()), 0)

    allowance = GuestWifiDailyAllowance.objects.select_for_update().get(pk=grant.allowance_id)
    allowance.consumed_seconds = int(allowance.consumed_seconds or 0) + seconds
    allowance.save(update_fields=['consumed_seconds', 'updated_at'])
    grant.usage_accounted_at = at
    grant.save(update_fields=['usage_accounted_at'])
    return seconds


@transaction.atomic
def pause_guest_wifi_session(session, *, actor=None, at=None, reason='upgraded_to_fast'):
    """Stop a basic session without discarding unused daily allowance."""
    at = at or timezone.now()
    session = InternetSession.objects.select_for_update().get(pk=session.pk)
    grant = GuestWifiGrant.objects.filter(session=session).first()
    if not grant:
        return session
    if session.status == InternetSession.Status.ACTIVE:
        session = end_usage_session(session, actor=actor, at=at)
        session.lifecycle_end_reason = reason
        session.save(update_fields=['lifecycle_end_reason', 'updated_at'])
        enqueue_session_network_operation(
            session,
            InternetSessionNetworkOperation.Operation.DISCONNECT,
            reason='basic Wi-Fi paused',
            process_after_commit=True,
        )
    account_guest_wifi_session_usage(session, at=at)
    return session


def _active_basic_session_for_allowance(allowance, at=None):
    at = at or timezone.now()
    grant = (
        GuestWifiGrant.objects.filter(
            allowance=allowance,
            session__status=InternetSession.Status.ACTIVE,
        )
        .select_related('session')
        .order_by('-created_at')
        .first()
    )
    if not grant:
        return None
    if grant.session.authorized_until and grant.session.authorized_until <= at:
        return None
    return grant.session


def _apply_bonus_to_active_session(bonus, allowance, at):
    session = _active_basic_session_for_allowance(allowance, at)
    if not session:
        return False
    session.authorized_minutes = int(session.authorized_minutes or 0) + int(bonus.minutes)
    update_fields = ['authorized_minutes', 'updated_at']
    state = _network_activation(session)
    if state and state.network_activated_at and session.authorized_until:
        session.authorized_until = session.authorized_until + timedelta(minutes=int(bonus.minutes))
        update_fields.append('authorized_until')
    session.save(update_fields=update_fields)
    bonus.applied_session = session
    bonus.applied_at = at
    bonus.save(update_fields=['applied_session', 'applied_at', 'updated_at'])
    return True


@transaction.atomic
def sync_order_bonus(order, *, at=None):
    """Award/revoke one capped basic-Internet extension when an order becomes eligible.

    Only accepted-or-later Hub orders qualify. A NEW cart/order does not extend
    Internet. Cancellation or falling below the configured threshold revokes an
    unapplied bonus; already-applied time is left intact rather than retroactively
    disconnecting a customer.
    """
    at = at or timezone.now()
    policy = get_guest_wifi_policy()
    if not policy.enabled or not policy.order_bonus_enabled:
        return None
    if not order or not order.pk or not order.visit_id:
        return None

    total = int(order.total_syp or 0)
    threshold = int(policy.qualifying_order_minimum_syp or 0)
    qualifies = (
        order.status in ELIGIBLE_ORDER_STATUSES
        and total > 0
        and total >= threshold
    )
    existing = GuestWifiOrderBonus.objects.select_for_update().filter(order_id=order.pk).first()

    if not qualifies:
        if existing and existing.revoked_at is None and existing.applied_at is None:
            allowance = GuestWifiDailyAllowance.objects.select_for_update().get(pk=existing.allowance_id)
            allowance.order_bonus_minutes_granted = max(
                int(allowance.order_bonus_minutes_granted or 0) - int(existing.minutes or 0),
                0,
            )
            allowance.save(update_fields=['order_bonus_minutes_granted', 'updated_at'])
            existing.revoked_at = at
            existing.save(update_fields=['revoked_at', 'updated_at'])
        return existing

    credential = (
        order.visit.browser_credentials.filter(revoked_at__isnull=True)
        .order_by('-last_seen_at', '-pk')
        .first()
    )
    if not credential:
        return None

    allowance = _allowance(credential, at=at, create=True, lock=True)
    if existing:
        if existing.revoked_at is not None and existing.applied_at is None:
            available = max(
                int(policy.daily_complimentary_minutes or 0) - int(allowance.total_granted_minutes),
                0,
            )
            restored = min(int(existing.minutes or 0), available)
            if restored <= 0:
                return existing
            existing.minutes = restored
            existing.allowance = allowance
            existing.credential = credential
            existing.business_date = allowance.business_date
            existing.order_total_syp_snapshot = total
            existing.revoked_at = None
            existing.save(update_fields=[
                'minutes', 'allowance', 'credential', 'business_date',
                'order_total_syp_snapshot', 'revoked_at', 'updated_at',
            ])
            allowance.order_bonus_minutes_granted = int(allowance.order_bonus_minutes_granted or 0) + restored
            allowance.save(update_fields=['order_bonus_minutes_granted', 'updated_at'])
            _apply_bonus_to_active_session(existing, allowance, at)
        return existing

    available = max(
        int(policy.daily_complimentary_minutes or 0) - int(allowance.total_granted_minutes),
        0,
    )
    minutes = min(int(policy.order_bonus_minutes or 0), available)
    if minutes <= 0:
        return None

    bonus = GuestWifiOrderBonus.objects.create(
        order=order,
        allowance=allowance,
        credential=credential,
        business_date=allowance.business_date,
        minutes=minutes,
        order_total_syp_snapshot=total,
    )
    allowance.order_bonus_minutes_granted = int(allowance.order_bonus_minutes_granted or 0) + minutes
    allowance.save(update_fields=['order_bonus_minutes_granted', 'updated_at'])
    applied = _apply_bonus_to_active_session(bonus, allowance, at)
    ActivityLog.objects.create(action='guest_wifi.order_bonus_granted', details={
        'order_id': order.pk,
        'visit_id': order.visit_id,
        'credential_id': credential.pk,
        'minutes': minutes,
        'applied_immediately': applied,
        'daily_cap_minutes': int(policy.daily_complimentary_minutes or 0),
    })
    return bonus
