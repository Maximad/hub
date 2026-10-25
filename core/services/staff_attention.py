"""Read-only, role-scoped attention projections for the staff Operations page.

The authoritative task/approval services own all state changes. This module
does not sync tasks, expire requests, post payments or touch a network backend.
"""
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from core.models import HubVisit, HubVisitBrowserCredential
from core.services.fast_internet_approval import approval_required, pending_approvals_queryset
from operations.models import BusinessDay, BusinessDayTask, OperationalTaskTemplate


ACTIVE_DAYS = (
    BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED,
)


def _active_day(*, now=None):
    # A previous Business Day can legitimately remain open for financial
    # reconciliation. Its unfinished tasks are *not* today's attention queue.
    # The original day and tasks remain available for an authorized review.
    from operations.services import current_business_date
    return BusinessDay.objects.filter(
        business_date=current_business_date(now),
        status__in=ACTIVE_DAYS,
    ).first()


def _is_task_manager(user):
    return bool(user.is_superuser or getattr(user, 'role', '') in {'admin', 'cashier'})


def _visible_pending_tasks(day, user):
    if not day:
        return BusinessDayTask.objects.none()
    tasks = BusinessDayTask.objects.filter(
        business_day=day, status=BusinessDayTask.Status.PENDING,
    )
    if _is_task_manager(user):
        return tasks
    # A role-matched task explicitly assigned to somebody else is not the
    # current user's personal actionable queue.
    tasks = tasks.filter(
        Q(assigned_to_id=user.pk)
        | Q(assigned_to__isnull=True, responsibility_role=getattr(user, 'role', '')),
    )
    # Source reviews require a checked-in operations-duty holder; ordinary
    # role matching does not authorize access to their confidential context.
    return tasks.exclude(source_type='OperationalReviewCase')


def get_staff_attention(user, capabilities, *, now=None):
    """Project urgent tasks and browser approvals; no DB writes."""
    now = now or timezone.now()
    day = _active_day(now=now)
    tasks_query = _visible_pending_tasks(day, user)
    # Show assigned, required, high-priority and imminently due work. Regular
    # undated checklist items stay on the full Tasks page, avoiding noise.
    tasks_query = tasks_query.filter(
        Q(assigned_to_id=user.pk)
        | Q(is_required=True)
        | Q(priority=OperationalTaskTemplate.Priority.HIGH)
        | Q(due_at__lte=now + timedelta(hours=2))
    )
    task_list = list(tasks_query.select_related('assigned_to').order_by(
        'due_at', 'created_at', 'pk',
    ))
    task_list.sort(key=lambda task: (
        0 if task.due_at and task.due_at <= now else 1,
        0 if task.priority == OperationalTaskTemplate.Priority.HIGH else 1,
        0 if task.is_required else 1,
        task.due_at or datetime.max.replace(tzinfo=now.tzinfo),
        task.pk,
    ))
    for task in task_list[:5]:
        task.attention_overdue = bool(task.due_at and task.due_at <= now)
        task.attention_is_review = task.source_type == 'OperationalReviewCase'
        task.attention_can_complete = (
            not task.attention_is_review
            and (_is_task_manager(user)
                 or task.assigned_to_id == user.pk
                 or (task.responsibility_role and task.responsibility_role == getattr(user, 'role', '')))
        )

    show_internet = bool(capabilities.get('members/internet') and approval_required())
    if show_internet:
        approvals = pending_approvals_queryset().select_related(
            'visit__table', 'visit__member',
        ).order_by(
            'fast_internet_requested_at', 'pk',
        )
        internet_count = approvals.count()
        internet_rows = list(approvals[:3])
    else:
        internet_count = 0
        internet_rows = []

    return {
        'tasks': task_list[:5],
        'tasks_count': len(task_list),
        'more_tasks': len(task_list) > 5,
        'internet_requests': internet_rows,
        'internet_count': internet_count,
        'more_internet': internet_count > 3,
        'business_day': day,
    }
