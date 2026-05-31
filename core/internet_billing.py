from math import ceil

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from members.models import MemberCreditLedger, MembershipSubscription


ADMIN_ROLES = {'admin', 'owner'}


def calculate_session_duration_minutes(started_at, ended_at):
    """Return the ceiling duration between datetimes in whole minutes."""
    if not started_at or not ended_at or ended_at <= started_at:
        return 0
    return max(ceil((ended_at - started_at).total_seconds() / 60), 0)


def calculate_metered_session_total(duration_minutes, rate_per_hour_syp, minimum_minutes=0, free_grace_minutes=0, daily_cap_syp=None):
    duration = max(int(duration_minutes or 0), 0)
    rate = max(int(rate_per_hour_syp or 0), 0)
    minimum = max(int(minimum_minutes or 0), 0)
    grace = max(int(free_grace_minutes or 0), 0)
    if duration <= grace or rate <= 0:
        return 0
    billable_minutes = max(duration - grace, 0)
    if billable_minutes > 0 and minimum:
        billable_minutes = max(billable_minutes, minimum)
    total = ceil((billable_minutes * rate) / 60)
    if daily_cap_syp not in (None, ''):
        total = min(total, max(int(daily_cap_syp), 0))
    return max(total, 0)


def calculate_billable_minutes(duration_minutes, minimum_minutes=0, free_grace_minutes=0):
    duration = max(int(duration_minutes or 0), 0)
    grace = max(int(free_grace_minutes or 0), 0)
    if duration <= grace:
        return 0
    billable = max(duration - grace, 0)
    minimum = max(int(minimum_minutes or 0), 0)
    return max(billable, minimum) if billable > 0 else 0


def can_override_session_total(user):
    return bool(user and (getattr(user, 'is_superuser', False) or getattr(user, 'role', '') in ADMIN_ROLES))


def active_subscription_for_member(member):
    now = timezone.now()
    return (
        MembershipSubscription.objects
        .filter(member=member, status='active', starts_at__lte=now)
        .filter(models_ends_filter(now))
        .order_by('-starts_at', '-created_at')
        .first()
    )


def models_ends_filter(now):
    from django.db.models import Q
    return Q(ends_at__isnull=True) | Q(ends_at__gte=now)


@transaction.atomic
def finalize_internet_session(session, ended_by, manual_total=None, override_reason=None, ended_at=None):
    """Finalize a manual internet/workspace billing session and safely deduct prepaid minutes."""
    from core.models import InternetSession

    if session.status != InternetSession.Status.ACTIVE:
        return session

    ended_at = ended_at or timezone.now()
    started_at = session.effective_started_at
    duration = calculate_session_duration_minutes(started_at, ended_at)
    calculated_total = 0
    if session.billing_mode not in {InternetSession.BillingMode.FREE, InternetSession.BillingMode.PREPAID}:
        calculated_total = calculate_metered_session_total(
            duration,
            session.rate_per_hour_syp,
            session.minimum_minutes,
            session.free_grace_minutes,
            session.daily_cap_syp,
        )

    if manual_total not in (None, ''):
        if not can_override_session_total(ended_by):
            raise PermissionDenied('تعديل مبلغ الجلسة يحتاج صلاحية مدير.')
        if not (override_reason or '').strip():
            raise ValidationError('سبب التعديل مطلوب عند تغيير مبلغ الجلسة يدوياً.')
        try:
            manual_total_value = int(manual_total)
        except (TypeError, ValueError):
            raise ValidationError('المبلغ اليدوي يجب أن يكون رقماً صحيحاً.')
        session.manual_total_syp = max(manual_total_value, 0)
        session.override_reason = override_reason.strip()

    if session.billing_mode == InternetSession.BillingMode.PREPAID and session.member_id:
        subscription = active_subscription_for_member(session.member)
        remaining = None if subscription is None else subscription.remaining_internet_minutes
        if subscription is None or remaining is None:
            raise ValidationError('لا يوجد رصيد دقائق فعّال لهذا العضو لإنهاء جلسة مسبقة الدفع.')
        if remaining < duration:
            raise ValidationError('رصيد دقائق العضو غير كافٍ، ولا يمكن أن يصبح الرصيد سالباً.')
        subscription.remaining_internet_minutes = remaining - duration
        subscription.save(update_fields=['remaining_internet_minutes', 'updated_at'])
        MemberCreditLedger.objects.create(
            member=session.member,
            subscription=subscription,
            change_type='use_minutes',
            minutes_delta=-duration,
            notes=f'استهلاك دقائق لجلسة إنترنت/عمل #{session.id}',
            created_by=ended_by if getattr(ended_by, 'is_authenticated', False) else None,
        )

    session.ended_at = ended_at
    session.end_time = ended_at
    session.duration_minutes = duration
    session.actual_duration_minutes = duration
    session.calculated_total_syp = calculated_total
    session.status = InternetSession.Status.ENDED if session.payable_total_syp == 0 else InternetSession.Status.UNPAID
    session.ended_by = ended_by if getattr(ended_by, 'is_authenticated', False) else None
    session.save(update_fields=[
        'ended_at', 'end_time', 'duration_minutes', 'actual_duration_minutes', 'calculated_total_syp',
        'manual_total_syp', 'override_reason', 'status', 'ended_by', 'updated_at',
    ])
    return session
