"""Independent, opt-in reminder lifecycle over existing Hub operational sources.

No new scheduler or message broker. business_day_tick invokes this service.
Only in-app notifications are created: a reminder is never a browser push.
BusinessDayTask remains authoritative about task completion and source status.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog, NotificationEvent, NotificationLog, NotificationRecipient
from .duty import ACTIVE_DUTY_STATUSES, current_duty_users
from .models import BusinessDay, BusinessDayTask, Reminder, ReminderOccurrence
from .services import current_business_date


def reminders_enabled():
    return bool(getattr(settings, 'REMINDERS_ENGINE_ENABLED', False))


def _manager(user):
    return bool(
        getattr(user, 'is_authenticated', False)
        and getattr(user, 'is_active', False)
        and user_has_capability(user, 'staff_home')
        and (user.is_superuser or getattr(user, 'role', '') in {'admin', 'cashier'})
    )


def _valid_datetime(value, label):
    if value is None or timezone.is_naive(value):
        raise ValidationError(f'{label} يجب أن يكون تاريخاً ووقتاً واضحاً.')
    return value


def _valid_repeat(repeat, interval_minutes):
    if repeat not in Reminder.Repeat.values:
        raise ValidationError('نوع تكرار التذكير غير صالح.')
    if repeat == Reminder.Repeat.INTERVAL and not (1 <= interval_minutes <= 10080):
        raise ValidationError('مدة التكرار يجب أن تكون بين دقيقة و7 أيام.')
    if repeat != Reminder.Repeat.INTERVAL and interval_minutes:
        raise ValidationError('تُستخدم الدقائق فقط مع التكرار بفترة.')


@transaction.atomic
def create_standalone_reminder(
    *, actor, title, due_at, details='', repeat=Reminder.Repeat.ONCE,
    interval_minutes=0, assigned_to=None, responsibility='', expires_at=None,
):
    """Create without modifying the Business Day, schedules or other modules."""
    if not _manager(actor):
        raise PermissionDenied
    title = (title or '').strip()
    if not title:
        raise ValidationError('عنوان التذكير مطلوب.')
    _valid_datetime(due_at, 'وقت التذكير')
    _valid_repeat(repeat, interval_minutes)
    if responsibility and responsibility not in {
        choice[0] for choice in Reminder._meta.get_field('responsibility').choices
    }:
        raise ValidationError('المسؤولية التشغيلية غير صالحة.')
    if expires_at is not None:
        _valid_datetime(expires_at, 'وقت انتهاء التذكير')
        if expires_at <= due_at:
            raise ValidationError('انتهاء التذكير يجب أن يكون بعد موعده.')
    if assigned_to is not None and (
        not assigned_to.is_active or not user_has_capability(assigned_to, 'staff_home')
    ):
        raise ValidationError('الموظف المحدد غير مؤهّل.')
    reminder = Reminder.objects.create(
        title_ar=title, details=(details or '').strip(), next_due_at=due_at,
        repeat=repeat, interval_minutes=interval_minutes,
        assigned_to=assigned_to, responsibility=responsibility,
        expires_at=expires_at, created_by=actor,
    )
    ActivityLog.objects.create(
        actor=actor, action='reminder.created',
        details={'reminder_id': reminder.pk, 'repeat': repeat},
    )
    return reminder


@transaction.atomic
def close_reminder(reminder, *, actor, cancelled=False):
    if not _manager(actor) and reminder.assigned_to_id != getattr(actor, 'pk', None):
        raise PermissionDenied
    reminder = Reminder.objects.select_for_update().get(pk=reminder.pk)
    if reminder.source_task_id:
        raise ValidationError('أغلق المهمة الأصلية من صفحة المهام بدلاً من إنهاء تذكيرها.')
    if reminder.status != Reminder.Status.ACTIVE:
        return reminder
    reminder.status = Reminder.Status.CANCELLED if cancelled else Reminder.Status.COMPLETED
    reminder.next_due_at = None
    reminder.save(update_fields=['status', 'next_due_at', 'updated_at'])
    ActivityLog.objects.create(
        actor=actor, action='reminder.closed',
        details={'reminder_id': reminder.pk, 'cancelled': cancelled},
    )
    return reminder


@transaction.atomic
def snooze_reminder(reminder, *, actor, until):
    if not _manager(actor) and reminder.assigned_to_id != getattr(actor, 'pk', None):
        raise PermissionDenied
    _valid_datetime(until, 'وقت التأجيل')
    if until <= timezone.now():
        raise ValidationError('يجب أن يكون موعد التأجيل في المستقبل.')
    reminder = Reminder.objects.select_for_update().get(pk=reminder.pk)
    if reminder.status != Reminder.Status.ACTIVE or reminder.source_task_id:
        raise ValidationError('لا يمكن تأجيل هذا التذكير.')
    if reminder.expires_at and until >= reminder.expires_at:
        raise ValidationError('لا يمكن تأجيل التذكير إلى ما بعد انتهاء صلاحيته.')
    reminder.next_due_at = until
    reminder.save(update_fields=['next_due_at', 'updated_at'])
    ActivityLog.objects.create(
        actor=actor, action='reminder.snoozed',
        details={'reminder_id': reminder.pk, 'until': until.isoformat()},
    )
    return reminder


def sync_task_reminders(day, *, repeat_minutes=60):
    """Derive task reminder schedules, never another operational task."""
    if day.status not in ACTIVE_DUTY_STATUSES:
        return 0
    if not 1 <= repeat_minutes <= 10080:
        raise ValidationError('فترة إعادة التذكير غير صالحة.')
    count = 0
    tasks = day.tasks.filter(
        status=BusinessDayTask.Status.PENDING, due_at__isnull=False,
    ).select_related('assigned_to')
    Reminder.objects.filter(
        source_task__business_day=day,
        status=Reminder.Status.ACTIVE,
    ).exclude(
        source_task__status=BusinessDayTask.Status.PENDING,
    ).update(status=Reminder.Status.COMPLETED, next_due_at=None, retry_after=None)

    for task in tasks:
        due = task.due_at
        if task.last_reminded_at:
            due = max(due, task.last_reminded_at + timedelta(minutes=repeat_minutes))
        reminder, created = Reminder.objects.get_or_create(
            source_task=task,
            defaults={
                'title_ar': task.title_ar,
                'details': '',
                'next_due_at': due,
                'source_due_at': task.due_at,
                'repeat': Reminder.Repeat.INTERVAL,
                'interval_minutes': repeat_minutes,
                'responsibility': _task_responsibility(task),
                'assigned_to': task.assigned_to,
            },
        )
        if created:
            count += 1
        else:
            changes = []
            values = {
                'title_ar': task.title_ar,
                'assigned_to': task.assigned_to,
                'responsibility': _task_responsibility(task),
                'interval_minutes': repeat_minutes,
            }
            if reminder.status == Reminder.Status.COMPLETED:
                values['status'] = Reminder.Status.ACTIVE
                values['next_due_at'] = due
                values['retry_after'] = None
            if reminder.source_due_at != task.due_at:
                values['source_due_at'] = task.due_at
                values['next_due_at'] = due
                values['retry_after'] = None
            for key, value in values.items():
                if getattr(reminder, key) != value:
                    setattr(reminder, key, value)
                    changes.append(key)
            if changes:
                reminder.save(update_fields=changes + ['updated_at'])
    return count


def _task_responsibility(task):
    role = task.responsibility_role or ''
    return {
        'cashier': 'cashier', 'waiter': 'service', 'service': 'service',
        'bartender': 'bar', 'bar': 'bar', 'kitchen': 'kitchen',
        'admin': 'operations', 'operations': 'operations',
    }.get(role, 'operations')


def _current_day(now):
    day = BusinessDay.objects.filter(
        business_date=current_business_date(now),
        status__in=ACTIVE_DUTY_STATUSES,
    ).first()
    return day


def _eligible_direct(day, user, responsibility):
    """Check current duty, not a historical check-in alone."""
    if user is None or not user.is_active or not user_has_capability(user, 'staff_home'):
        return False
    slots = (
        [responsibility] if responsibility
        else ['service', 'cashier', 'bar', 'kitchen', 'operations']
    )
    return any(
        holder.pk == user.pk
        for slot in slots for holder in current_duty_users(day, slot)
    )


def _recipient(reminder, day):
    candidate = reminder.assigned_to
    if candidate is not None and _eligible_direct(day, candidate, reminder.responsibility):
        return candidate
    slot = reminder.responsibility or 'operations'
    holders = current_duty_users(day, slot)
    if holders and user_has_capability(holders[0], 'staff_home'):
        return holders[0]
    if slot != 'operations':
        holders = current_duty_users(day, 'operations')
        if holders and user_has_capability(holders[0], 'staff_home'):
            return holders[0]
    return None


def _next_due(reminder, scheduled_for, now):
    if reminder.repeat == Reminder.Repeat.ONCE:
        return None
    if reminder.repeat == Reminder.Repeat.INTERVAL:
        period = timedelta(minutes=reminder.interval_minutes)
        jumps = int((now - scheduled_for).total_seconds() // period.total_seconds()) + 1
        return scheduled_for + jumps * period
    # Local calendar time survives a change in the UTC offset at DST boundaries.
    local = timezone.localtime(scheduled_for)
    local_now = timezone.localtime(now)
    step = 1 if reminder.repeat == Reminder.Repeat.DAILY else 7
    elapsed_days = max((local_now.date() - local.date()).days, 0)
    number = elapsed_days // step
    zone = timezone.get_current_timezone()
    while True:
        day = local.date() + timedelta(days=(number * step))
        target = timezone.make_aware(datetime.combine(day, local.time().replace(tzinfo=None)), zone)
        if target > now:
            return target
        number += 1


@transaction.atomic
def tick_reminders(*, now=None, limit=100):
    """Process at most one due occurrence per reminder with row locks.

    Disabled, uncovered or source-resolved reminders cannot send. Multiple
    ticks are safe: the next due field and unique occurrence ledger are written
    atomically with the in-app event.
    """
    result = {'sent': 0, 'deferred': 0, 'source_resolved': 0, 'expired': 0}
    if not reminders_enabled():
        return result
    now = now or timezone.now()
    day = _current_day(now)
    if day is None:
        return result
    due = Reminder.objects.select_for_update(of=('self',)).filter(
        status=Reminder.Status.ACTIVE, next_due_at__isnull=False, next_due_at__lte=now,
    ).filter(
        Q(retry_after__isnull=True) | Q(retry_after__lte=now)
    ).select_related('source_task', 'source_task__business_day', 'assigned_to').order_by('next_due_at', 'pk')[:limit]
    for reminder in due:
        task = reminder.source_task
        if task is not None and (
            task.status != BusinessDayTask.Status.PENDING
            or task.business_day_id != day.pk
            or task.business_day.status not in ACTIVE_DUTY_STATUSES
        ):
            reminder.status = Reminder.Status.COMPLETED
            reminder.next_due_at = None
            reminder.retry_after = None
            reminder.save(update_fields=['status', 'next_due_at', 'retry_after', 'updated_at'])
            result['source_resolved'] += 1
            continue
        if reminder.expires_at and reminder.expires_at <= now:
            reminder.status = Reminder.Status.CANCELLED
            reminder.next_due_at = None
            reminder.retry_after = None
            reminder.save(update_fields=['status', 'next_due_at', 'retry_after', 'updated_at'])
            result['expired'] += 1
            continue
        recipient = _recipient(reminder, day)
        if recipient is None:
            # The due time stays unchanged; retry suppression is independent
            # of daily/weekly calendar recurrence and avoids queue starvation.
            reminder.retry_after = now + timedelta(minutes=5)
            reminder.save(update_fields=['retry_after', 'updated_at'])
            result['deferred'] += 1
            continue
        scheduled_for = reminder.next_due_at
        # Lock and append proof before advancing recurrence; a second worker
        # cannot create the same delivery under a concurrent tick.
        if ReminderOccurrence.objects.filter(
            reminder=reminder, scheduled_for=scheduled_for,
        ).exists():
            reminder.next_due_at = _next_due(reminder, scheduled_for, now)
            reminder.retry_after = None
            reminder.save(update_fields=['next_due_at', 'retry_after', 'updated_at'])
            continue
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.OPERATIONAL_REMINDER,
            title_ar=f'تذكير: {reminder.title_ar}'[:160],
            message_ar='يوجد عمل يحتاج متابعة داخل هَبّ.',
        )
        NotificationRecipient.objects.create(
            notification_event=event, user=recipient,
            role=getattr(recipient, 'role', '') or '',
        )
        NotificationLog.objects.create(
            notification_event=event,
            channel=NotificationLog.Channel.SYSTEM,
            recipient_user=recipient,
            recipient_role=getattr(recipient, 'role', '') or '',
            status=NotificationLog.Status.SENT, sent_at=now,
        )
        ReminderOccurrence.objects.create(
            reminder=reminder, scheduled_for=scheduled_for,
            notification_event=event, recipient_user=recipient,
        )
        reminder.next_due_at = _next_due(reminder, scheduled_for, now)
        reminder.retry_after = None
        reminder.save(update_fields=['next_due_at', 'retry_after', 'updated_at'])
        if task is not None:
            task.last_reminded_at = now
            task.reminder_count += 1
            task.save(update_fields=['last_reminded_at', 'reminder_count', 'updated_at'])
        result['sent'] += 1
    return result
