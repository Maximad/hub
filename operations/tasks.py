import uuid
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.models import ActivityLog, InventoryItem
from events.models import Event
from reservations.models import Reservation

from .config import int_setting
from .models import BusinessDay, BusinessDayTask, OperationalTaskTemplate
from .opening import _notify_users
from .services import business_day_bounds


def _audit(actor, action, **details):
    ActivityLog.objects.create(
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        action=action,
        details=details,
    )


def _local_due(day, clock_time):
    if clock_time is None:
        return None
    due_date = day.business_date
    if clock_time.hour < int(day.cutoff_hour):
        due_date += timedelta(days=1)
    return timezone.make_aware(
        datetime.combine(due_date, clock_time),
        timezone=timezone.get_current_timezone(),
    )


def _template_applies(template, business_date):
    if not template.is_active:
        return False
    if template.active_from and business_date < template.active_from:
        return False
    if template.active_until and business_date > template.active_until:
        return False
    weekdays = template.weekdays or []
    if weekdays and business_date.weekday() not in weekdays:
        return False
    return True


def _upsert_generated_task(day, *, fingerprint, defaults):
    task, created = BusinessDayTask.objects.get_or_create(
        business_day=day,
        fingerprint=fingerprint,
        defaults=defaults,
    )
    if not created and task.status == BusinessDayTask.Status.PENDING:
        changed = []
        for field in (
            'title_ar', 'details', 'priority', 'due_at', 'is_required',
            'responsibility_role', 'source_type', 'source_id', 'metadata', 'task_template',
        ):
            value = defaults.get(field)
            if getattr(task, field) != value:
                setattr(task, field, value)
                changed.append(field)
        if changed:
            changed.append('updated_at')
            task.save(update_fields=changed)
    return task, created


def sync_business_day_tasks(day, *, actor=None):
    """Idempotently materialize recurring and source-driven operational tasks.

    This reads events, reservations, and low-stock state only. It never mutates
    those source systems or creates purchases/accounting records.
    """
    if day.status == BusinessDay.Status.CLOSED:
        return {'created': 0, 'active': 0, 'auto_waived': 0}

    start, end = business_day_bounds(day)
    active_fingerprints = set()
    created_count = 0

    for template in OperationalTaskTemplate.objects.filter(is_active=True).order_by('pk'):
        if not _template_applies(template, day.business_date):
            continue
        fingerprint = f'template:{template.pk}'
        active_fingerprints.add(fingerprint)
        _, created = _upsert_generated_task(
            day,
            fingerprint=fingerprint,
            defaults={
                'kind': BusinessDayTask.Kind.RECURRING,
                'title_ar': template.title_ar,
                'details': template.details,
                'priority': template.priority,
                'due_at': _local_due(day, template.due_time),
                'is_required': template.is_required,
                'responsibility_role': template.responsibility_role,
                'task_template': template,
                'source_type': 'OperationalTaskTemplate',
                'source_id': str(template.pk),
                'metadata': {'weekdays': template.weekdays or []},
            },
        )
        created_count += int(created)

    event_lead = int_setting('BUSINESS_DAY_EVENT_PREP_LEAD_MINUTES', 120, minimum=0, maximum=1440)
    events = Event.objects.filter(
        status=Event.Status.PUBLISHED,
        starts_at__gte=start,
        starts_at__lt=end,
    ).select_related('room').order_by('starts_at')
    for event in events:
        fingerprint = f'event:{event.pk}:prep'
        active_fingerprints.add(fingerprint)
        room_label = str(event.room) if event.room_id else 'بدون مساحة محددة'
        capacity = f' — السعة {event.capacity}' if event.capacity else ''
        _, created = _upsert_generated_task(
            day,
            fingerprint=fingerprint,
            defaults={
                'kind': BusinessDayTask.Kind.EVENT,
                'title_ar': f'تحضير فعالية: {event.title_ar}',
                'details': f'{room_label}{capacity}',
                'priority': OperationalTaskTemplate.Priority.HIGH,
                'due_at': event.starts_at - timedelta(minutes=event_lead),
                'is_required': True,
                'responsibility_role': '',
                'source_type': 'Event',
                'source_id': str(event.pk),
                'metadata': {
                    'starts_at': event.starts_at.isoformat(),
                    'room_id': event.room_id,
                    'capacity': event.capacity,
                },
            },
        )
        created_count += int(created)

    reservation_lead = int_setting('BUSINESS_DAY_RESERVATION_PREP_LEAD_MINUTES', 30, minimum=0, maximum=720)
    reservation_dates = [day.business_date, day.business_date + timedelta(days=1)]
    reservations = Reservation.objects.filter(
        reservation_type=Reservation.ReservationType.REGULAR,
        status=Reservation.Status.CONFIRMED,
        reservation_date__in=reservation_dates,
    ).select_related('room', 'table_area', 'table_area__room').order_by('reservation_date', 'start_time')
    for reservation in reservations:
        if not reservation.start_time or not reservation.reservation_date:
            continue
        starts_at = timezone.make_aware(
            datetime.combine(reservation.reservation_date, reservation.start_time),
            timezone=timezone.get_current_timezone(),
        )
        if not (start <= starts_at < end):
            continue
        fingerprint = f'reservation:{reservation.pk}:prep'
        active_fingerprints.add(fingerprint)
        room = reservation.effective_room
        room_label = str(room) if room else 'بدون مساحة محددة'
        _, created = _upsert_generated_task(
            day,
            fingerprint=fingerprint,
            defaults={
                'kind': BusinessDayTask.Kind.RESERVATION,
                'title_ar': f'تحضير حجز: {reservation.name} — {reservation.party_size} ضيف',
                'details': f'{room_label} — {reservation.phone}',
                'priority': OperationalTaskTemplate.Priority.NORMAL,
                'due_at': starts_at - timedelta(minutes=reservation_lead),
                'is_required': False,
                'responsibility_role': '',
                'source_type': 'Reservation',
                'source_id': str(reservation.pk),
                'metadata': {
                    'starts_at': starts_at.isoformat(),
                    'party_size': reservation.party_size,
                    'room_id': getattr(room, 'pk', None),
                },
            },
        )
        created_count += int(created)

    low_items = list(
        InventoryItem.objects.filter(
            is_active=True,
            low_stock_threshold__isnull=False,
            current_quantity__lte=F('low_stock_threshold'),
        ).order_by('name_ar', 'pk')
    )
    inventory_fingerprint = 'inventory:low-stock-review'
    if low_items:
        active_fingerprints.add(inventory_fingerprint)
        labels = [f'{item.name_ar}: {item.current_quantity} / {item.low_stock_threshold}' for item in low_items]
        _, created = _upsert_generated_task(
            day,
            fingerprint=inventory_fingerprint,
            defaults={
                'kind': BusinessDayTask.Kind.INVENTORY,
                'title_ar': f'مراجعة المواد المنخفضة في المخزون ({len(low_items)})',
                'details': '؛ '.join(labels[:20]),
                'priority': OperationalTaskTemplate.Priority.HIGH,
                'due_at': _local_due(day, datetime.strptime('12:00', '%H:%M').time()),
                'is_required': False,
                'responsibility_role': '',
                'source_type': 'InventoryItem',
                'source_id': '',
                'metadata': {'item_ids': [item.pk for item in low_items], 'count': len(low_items)},
            },
        )
        created_count += int(created)

    generated_kinds = [
        BusinessDayTask.Kind.RECURRING,
        BusinessDayTask.Kind.EVENT,
        BusinessDayTask.Kind.RESERVATION,
        BusinessDayTask.Kind.INVENTORY,
    ]
    stale = BusinessDayTask.objects.filter(
        business_day=day,
        status=BusinessDayTask.Status.PENDING,
        kind__in=generated_kinds,
    ).exclude(fingerprint__in=active_fingerprints)
    auto_waived = stale.count()
    if auto_waived:
        stale.update(
            status=BusinessDayTask.Status.WAIVED,
            completion_note='أُغلقت تلقائياً بعد زوال سبب المهمة من النظام.',
            completed_at=timezone.now(),
            completed_by=actor if getattr(actor, 'is_authenticated', False) else None,
            updated_at=timezone.now(),
        )

    if created_count or auto_waived:
        _audit(
            actor,
            'business_day.tasks_synced',
            business_day_id=day.pk,
            created_count=created_count,
            active_count=len(active_fingerprints),
            auto_waived_count=auto_waived,
        )
    return {'created': created_count, 'active': len(active_fingerprints), 'auto_waived': auto_waived}


@transaction.atomic
def create_manual_task(day, *, actor, title, details='', due_at=None, priority='normal', is_required=False, assigned_to=None):
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('لا يمكن إضافة مهمة إلى يوم مغلق.')
    title = (title or '').strip()
    if not title:
        raise ValidationError('عنوان المهمة مطلوب.')
    if priority not in OperationalTaskTemplate.Priority.values:
        priority = OperationalTaskTemplate.Priority.NORMAL
    task = BusinessDayTask.objects.create(
        business_day=day,
        fingerprint=f'manual:{uuid.uuid4().hex}',
        kind=BusinessDayTask.Kind.MANUAL,
        title_ar=title,
        details=(details or '').strip(),
        due_at=due_at,
        priority=priority,
        is_required=bool(is_required),
        assigned_to=assigned_to,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    _audit(actor, 'business_day.task_created', business_day_id=day.pk, task_id=task.pk)
    return task


@transaction.atomic
def create_task_template(*, actor, title, details='', weekdays=None, due_time=None, priority='normal', responsibility_role='', is_required=False):
    title = (title or '').strip()
    if not title:
        raise ValidationError('عنوان المهمة الدورية مطلوب.')
    normalized_days = []
    for raw in weekdays or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 6 and value not in normalized_days:
            normalized_days.append(value)
    if priority not in OperationalTaskTemplate.Priority.values:
        priority = OperationalTaskTemplate.Priority.NORMAL
    template = OperationalTaskTemplate.objects.create(
        title_ar=title,
        details=(details or '').strip(),
        weekdays=sorted(normalized_days),
        due_time=due_time,
        priority=priority,
        responsibility_role=(responsibility_role or '').strip(),
        is_required=bool(is_required),
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    _audit(actor, 'business_day.task_template_created', task_template_id=template.pk)
    return template


@transaction.atomic
def set_task_status(task, *, actor, status, note=''):
    task = BusinessDayTask.objects.select_for_update().select_related('business_day').get(pk=task.pk)
    if task.business_day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('لا يمكن تعديل مهمة يوم مغلق.')
    if status not in BusinessDayTask.Status.values:
        raise ValidationError('حالة المهمة غير صالحة.')
    note = (note or '').strip()
    if status == BusinessDayTask.Status.WAIVED and not note:
        raise ValidationError('سبب إغلاق المهمة دون تنفيذ مطلوب.')
    task.status = status
    task.completion_note = note
    if status == BusinessDayTask.Status.PENDING:
        task.completed_at = None
        task.completed_by = None
    else:
        task.completed_at = timezone.now()
        task.completed_by = actor if getattr(actor, 'is_authenticated', False) else None
    task.save(update_fields=['status', 'completion_note', 'completed_at', 'completed_by', 'updated_at'])
    _audit(actor, 'business_day.task_status_changed', task_id=task.pk, status=status)
    return task


@transaction.atomic
def assign_task(task, *, actor, assigned_to):
    task = BusinessDayTask.objects.select_for_update().select_related('business_day').get(pk=task.pk)
    if task.business_day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('لا يمكن تعديل مهمة يوم مغلق.')
    task.assigned_to = assigned_to
    task.save(update_fields=['assigned_to', 'updated_at'])
    _audit(actor, 'business_day.task_assigned', task_id=task.pk, assigned_to_id=getattr(assigned_to, 'pk', None))
    return task


def send_overdue_task_reminders(day, *, actor=None, min_interval_minutes=None):
    if day.status == BusinessDay.Status.CLOSED:
        return 0
    now = timezone.now()
    interval = int_setting(
        'BUSINESS_DAY_TASK_REMINDER_MINUTES',
        min_interval_minutes if min_interval_minutes is not None else 60,
        minimum=1,
        maximum=1440,
    )
    threshold = now - timedelta(minutes=interval)
    tasks = list(
        day.tasks.filter(status=BusinessDayTask.Status.PENDING, due_at__isnull=False, due_at__lte=now)
        .select_related('assigned_to')
        .order_by('due_at', '-priority')
    )
    notified_tasks = 0
    User = get_user_model()
    for task in tasks:
        if task.last_reminded_at and task.last_reminded_at > threshold:
            continue
        recipients = []
        if task.assigned_to_id and task.assigned_to.is_active:
            recipients = [task.assigned_to]
        elif task.responsibility_role:
            recipients = list(
                User.objects.filter(
                    is_active=True,
                    business_day_assignments__business_day=day,
                    business_day_assignments__role_snapshot=task.responsibility_role,
                ).distinct()
            )
        if not recipients:
            recipients = list(
                User.objects.filter(is_active=True).filter(
                    Q(is_superuser=True) | Q(role__in=['admin', 'cashier'])
                ).distinct()
            )
        if recipients:
            _notify_users(
                recipients,
                title=f'مهمة تشغيلية متأخرة: {task.title_ar}',
                message='تجاوزت المهمة وقتها المحدد وما زالت مفتوحة. راجع صفحة المهام التشغيلية.',
                actor=actor,
            )
            task.last_reminded_at = now
            task.reminder_count += 1
            task.save(update_fields=['last_reminded_at', 'reminder_count', 'updated_at'])
            notified_tasks += 1
    if notified_tasks:
        _audit(actor, 'business_day.task_reminders_sent', business_day_id=day.pk, task_count=notified_tasks)
    return notified_tasks
