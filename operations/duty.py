"""Opt-in Active Duty state built on BusinessDayStaffAssignment.

Not wired to notifications or reminders yet: no change to live push routing.
A roster means scheduled, check-in means present, coverage means responsible.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog
from .models import BusinessDay, BusinessDayDutyCoverage, BusinessDayStaffAssignment


ACTIVE_DUTY_STATUSES = {
    BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED,
}


RESPONSIBILITY_CAPABILITY = {
    BusinessDayDutyCoverage.Responsibility.SERVICE: 'orders',
    BusinessDayDutyCoverage.Responsibility.CASHIER: 'cashier',
    BusinessDayDutyCoverage.Responsibility.BAR: 'kitchen_board',
    BusinessDayDutyCoverage.Responsibility.KITCHEN: 'kitchen_board',
    BusinessDayDutyCoverage.Responsibility.OPERATIONS: 'staff_home',
}


def _manager(user):
    return bool(
        getattr(user, 'is_authenticated', False)
        and getattr(user, 'is_active', False)
        and user_has_capability(user, 'staff_home')
        and (user.is_superuser or getattr(user, 'role', '') in {'admin', 'cashier'})
    )


def _require_open_day(day):
    if day.status not in ACTIVE_DUTY_STATUSES:
        raise ValidationError('تغيير مسؤوليات المناوبة متاح خلال يوم تشغيل مفتوح فقط.')


def _eligible_assignment(day, responsibility, user_id):
    if responsibility not in RESPONSIBILITY_CAPABILITY:
        raise ValidationError('المسؤولية التشغيلية غير معروفة.')
    try:
        normalized_user_id = int(user_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError('الموظف المحدد غير صالح.') from exc
    assignment = (
        BusinessDayStaffAssignment.objects.select_related('user')
        .filter(business_day=day, user_id=normalized_user_id)
        .first()
    )
    if (
        assignment is None
        or not assignment.user.is_active
        or assignment.checked_in_at is None
        or not user_has_capability(assignment.user, RESPONSIBILITY_CAPABILITY[responsibility])
    ):
        raise ValidationError('اختر موظفاً من فريق اليوم أكّد حضوره ويملك الصلاحية المطلوبة.')
    return assignment


def eligible_duty_users(day, responsibility):
    """Candidates for explicit coverage, not an auto-assign/broadcast fallback."""
    capability = RESPONSIBILITY_CAPABILITY.get(responsibility)
    if not capability or day.status not in ACTIVE_DUTY_STATUSES:
        return []
    assignments = (
        day.staff_assignments.filter(checked_in_at__isnull=False, user__is_active=True)
        .select_related('user').order_by('user__first_name', 'user__username')
    )
    return [a.user for a in assignments if user_has_capability(a.user, capability)]


def current_duty_users(day, responsibility):
    """Fail closed when coverage is unset, pending, invalid or day is inactive."""
    if day is None or day.status not in {BusinessDay.Status.OPEN, BusinessDay.Status.REOPENED}:
        return []
    coverage = (
        day.duty_coverages.filter(responsibility=responsibility)
        .select_related('primary_assignment__user')
        .first()
    )
    if coverage is None or coverage.primary_assignment_id is None:
        return []
    try:
        holder = _eligible_assignment(
            day, responsibility, coverage.primary_assignment.user_id
        )
    except ValidationError:
        return []
    if holder.pk != coverage.primary_assignment_id:
        return []
    return [holder.user]


def _audit(actor, action, day, responsibility, **details):
    ActivityLog.objects.create(
        actor=actor,
        action=action,
        details={'business_day_id': day.pk, 'responsibility': responsibility, **details},
    )


@transaction.atomic
def assign_duty_coverage(day, *, responsibility, user_id, actor):
    """Manager-authorized activation or takeover.

    A takeover invalidates any pending offer and leaves an audit record.
    It does not rewrite tasks, orders, push subscriptions or staff shifts.
    """
    if not _manager(actor):
        raise PermissionDenied
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    _require_open_day(day)
    candidate = _eligible_assignment(day, responsibility, user_id)
    coverage, _ = BusinessDayDutyCoverage.objects.select_for_update().get_or_create(
        business_day=day, responsibility=responsibility
    )
    old_user_id = coverage.primary_assignment.user_id if coverage.primary_assignment_id else None
    if coverage.primary_assignment_id == candidate.pk and coverage.proposed_assignment_id is None:
        return coverage
    coverage.primary_assignment = candidate
    coverage.proposed_assignment = None
    coverage.proposed_by = None
    coverage.proposed_at = None
    coverage.transferred_at = timezone.now()
    coverage.save()
    _audit(
        actor, 'business_day.duty_assigned', day, responsibility,
        from_user_id=old_user_id, to_user_id=candidate.user_id,
    )
    return coverage


@transaction.atomic
def request_duty_handover(day, *, responsibility, to_user_id, actor):
    """Current holder proposes a replacement, without relinquishing duty."""
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    _require_open_day(day)
    coverage = (
        BusinessDayDutyCoverage.objects.select_for_update()
        .filter(business_day=day, responsibility=responsibility).first()
    )
    if not coverage or not coverage.primary_assignment_id:
        raise ValidationError('لا يوجد مسؤول حالي لهذه المهمة.')
    if not getattr(actor, 'is_active', False) or coverage.primary_assignment.user_id != actor.pk:
        raise PermissionDenied
    candidate = _eligible_assignment(day, responsibility, to_user_id)
    if candidate.pk == coverage.primary_assignment_id:
        raise ValidationError('لا يمكن تسليم المسؤولية إلى الشخص الحالي نفسه.')
    if coverage.proposed_assignment_id == candidate.pk:
        return coverage
    coverage.proposed_assignment = candidate
    coverage.proposed_by = actor
    coverage.proposed_at = timezone.now()
    coverage.save(update_fields=['proposed_assignment', 'proposed_by', 'proposed_at', 'updated_at'])
    _audit(
        actor, 'business_day.duty_handover_requested', day, responsibility,
        from_user_id=actor.pk, to_user_id=candidate.user_id,
    )
    return coverage


@transaction.atomic
def accept_duty_handover(day, *, responsibility, actor):
    """Only the proposed, checked-in recipient can accept a live handover."""
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    _require_open_day(day)
    coverage = (
        BusinessDayDutyCoverage.objects.select_for_update()
        .filter(business_day=day, responsibility=responsibility).first()
    )
    if not coverage or not coverage.proposed_assignment_id:
        raise ValidationError('لا يوجد تسليم معلّق لهذه المسؤولية.')
    if not getattr(actor, 'is_active', False) or coverage.proposed_assignment.user_id != actor.pk:
        raise PermissionDenied
    candidate = _eligible_assignment(day, responsibility, actor.pk)
    if candidate.pk != coverage.proposed_assignment_id:
        raise ValidationError('طلب التسليم لم يعد صالحاً.')
    from_user_id = coverage.primary_assignment.user_id if coverage.primary_assignment_id else None
    coverage.primary_assignment = candidate
    coverage.proposed_assignment = None
    coverage.proposed_by = None
    coverage.proposed_at = None
    coverage.transferred_at = timezone.now()
    coverage.save()
    _audit(
        actor, 'business_day.duty_handover_accepted', day, responsibility,
        from_user_id=from_user_id, to_user_id=actor.pk,
    )
    return coverage
