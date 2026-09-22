from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import ActivityLog, NotificationEvent, NotificationLog, NotificationRecipient

from .models import (
    BusinessDay,
    BusinessDayChecklistItem,
    BusinessDayStaffAssignment,
    HandoverNote,
    StaffDailyCodeReceipt,
)


DEFAULT_OPENING_CHECKLIST = (
    ('handover_review', 'مراجعة ملاحظات التسليم المفتوحة من الأيام السابقة', True),
    ('staff_roster', 'تأكيد فريق العمل الموجود اليوم', True),
    ('opening_cash', 'تأكيد الصناديق والمبالغ الافتتاحية قبل بدء البيع', True),
    ('venue_ready', 'تأكيد ترتيب ونظافة وجاهزية المساحة', True),
    ('bar_kitchen_ready', 'تأكيد جاهزية البار/المطبخ والمواد الأساسية', True),
    ('internet_status', 'مراجعة حالة الإنترنت من واجهة هَبّ دون تغيير إعدادات الشبكة', True),
    ('product_availability', 'مراجعة المنتجات غير المتاحة أو الناقصة لليوم', True),
)


def _audit(actor, action, **details):
    ActivityLog.objects.create(actor=actor if getattr(actor, 'is_authenticated', False) else None, action=action, details=details)


def active_staff_queryset():
    User = get_user_model()
    provider_role = getattr(User.Role, 'INTERNET_PROVIDER', 'internet_provider')
    return User.objects.filter(is_active=True).exclude(role=provider_role).order_by('first_name', 'username', 'pk')


def on_duty_users(day):
    assignment_user_ids = list(day.staff_assignments.values_list('user_id', flat=True))
    if assignment_user_ids:
        return active_staff_queryset().filter(pk__in=assignment_user_ids)
    return active_staff_queryset()


def _notify_users(users, *, title, message, actor=None):
    users = list(users)
    if not users:
        return None
    event = NotificationEvent.objects.create(
        event_type=NotificationEvent.EventType.CLOSE_DAY_FINALIZED,
        title_ar=title,
        message_ar=message,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    NotificationRecipient.objects.bulk_create([
        NotificationRecipient(
            notification_event=event,
            user=user,
            role=getattr(user, 'role', '') or '',
        )
        for user in users
    ])
    NotificationLog.objects.create(
        notification_event=event,
        channel=NotificationLog.Channel.SYSTEM,
        recipient_role='operations',
        status=NotificationLog.Status.SENT,
        sent_at=timezone.now(),
    )
    return event


def seed_opening_checklist(day):
    created = 0
    for sort_order, (code, label, required) in enumerate(DEFAULT_OPENING_CHECKLIST, start=10):
        _, was_created = BusinessDayChecklistItem.objects.get_or_create(
            business_day=day,
            stage=BusinessDayChecklistItem.Stage.OPENING,
            code=code,
            defaults={
                'label_ar': label,
                'is_required': required,
                'sort_order': sort_order,
            },
        )
        created += int(was_created)
    return created


def _refresh_opening_completion(day, *, actor=None):
    pending_required = day.checklist_items.filter(
        stage=BusinessDayChecklistItem.Stage.OPENING,
        is_required=True,
        status=BusinessDayChecklistItem.Status.PENDING,
    ).exists()
    if pending_required:
        if day.opening_completed_at is not None or day.opening_completed_by_id is not None:
            day.opening_completed_at = None
            day.opening_completed_by = None
            day.save(update_fields=['opening_completed_at', 'opening_completed_by', 'updated_at'])
        return False
    if day.opening_completed_at is None:
        day.opening_completed_at = timezone.now()
        day.opening_completed_by = actor if getattr(actor, 'is_authenticated', False) else None
        day.save(update_fields=['opening_completed_at', 'opening_completed_by', 'updated_at'])
        _audit(actor, 'business_day.opening_completed', business_day_id=day.pk)
    return True


@transaction.atomic
def set_staff_roster(day, *, actor, user_ids):
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('لا يمكن تعديل فريق يوم مغلق.')
    normalized = []
    for raw in user_ids or []:
        try:
            normalized.append(int(raw))
        except (TypeError, ValueError):
            continue
    normalized = list(dict.fromkeys(normalized))
    staff = list(active_staff_queryset().filter(pk__in=normalized))
    if not staff:
        raise ValidationError('اختر موظفاً واحداً على الأقل لفريق اليوم.')
    valid_ids = {user.pk for user in staff}
    if valid_ids != set(normalized):
        raise ValidationError('تتضمن قائمة الفريق حساباً غير صالح أو غير نشط.')

    current_ids = set(day.staff_assignments.values_list('user_id', flat=True))
    day.staff_assignments.exclude(user_id__in=valid_ids).delete()
    for user in staff:
        BusinessDayStaffAssignment.objects.get_or_create(
            business_day=day,
            user=user,
            defaults={
                'role_snapshot': getattr(user, 'role', '') or '',
                'assigned_by': actor,
            },
        )

    roster_item = day.checklist_items.filter(
        stage=BusinessDayChecklistItem.Stage.OPENING,
        code='staff_roster',
    ).first()
    if roster_item:
        roster_item.status = BusinessDayChecklistItem.Status.DONE
        roster_item.completed_by = actor
        roster_item.completed_at = timezone.now()
        roster_item.note = f'{len(staff)} موظف/موظفة ضمن فريق اليوم.'
        roster_item.save(update_fields=['status', 'completed_by', 'completed_at', 'note', 'updated_at'])
    _refresh_opening_completion(day, actor=actor)

    newly_added = [user for user in staff if user.pk not in current_ids]
    if newly_added and day.code_version:
        _notify_users(
            newly_added,
            title=f'أنت ضمن فريق يوم {day.business_date}',
            message='رمز الفريق اليومي جاهز داخل صفحة اليوم التشغيلي. افتح هَبّ لعرضه بعد تسجيل الدخول.',
            actor=actor,
        )
        now = timezone.now()
        for user in newly_added:
            receipt, _ = StaffDailyCodeReceipt.objects.get_or_create(
                business_day=day,
                user=user,
                defaults={'code_version': day.code_version},
            )
            receipt.code_version = day.code_version
            receipt.notified_at = now
            receipt.save(update_fields=['code_version', 'notified_at', 'updated_at'])

    _audit(actor, 'business_day.staff_roster_changed', business_day_id=day.pk, user_ids=sorted(valid_ids))
    return day.staff_assignments.select_related('user').all()


@transaction.atomic
def update_checklist_item(item, *, actor, status, note=''):
    item = BusinessDayChecklistItem.objects.select_for_update().select_related('business_day').get(pk=item.pk)
    day = item.business_day
    if day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('لا يمكن تعديل قائمة افتتاح يوم مغلق.')
    valid_statuses = {
        BusinessDayChecklistItem.Status.PENDING,
        BusinessDayChecklistItem.Status.DONE,
        BusinessDayChecklistItem.Status.WAIVED,
    }
    if status not in valid_statuses:
        raise ValidationError('حالة البند غير صالحة.')
    note = (note or '').strip()
    if status == BusinessDayChecklistItem.Status.WAIVED and not note:
        raise ValidationError('سبب التجاوز مطلوب.')
    if item.code == 'staff_roster' and status != BusinessDayChecklistItem.Status.DONE:
        raise ValidationError('بند فريق اليوم يُحدّث من قائمة الموظفين وليس يدوياً.')

    item.status = status
    item.note = note
    if status == BusinessDayChecklistItem.Status.PENDING:
        item.completed_by = None
        item.completed_at = None
    else:
        item.completed_by = actor
        item.completed_at = timezone.now()
    item.save(update_fields=['status', 'note', 'completed_by', 'completed_at', 'updated_at'])
    _refresh_opening_completion(day, actor=actor)
    _audit(
        actor,
        'business_day.opening_checklist_changed',
        business_day_id=day.pk,
        checklist_item_id=item.pk,
        checklist_code=item.code,
        status=status,
    )
    return item


@transaction.atomic
def create_handover_note(day, *, actor, message, priority=HandoverNote.Priority.NORMAL, assigned_to=None):
    message = (message or '').strip()
    if not message:
        raise ValidationError('نص ملاحظة التسليم مطلوب.')
    if priority not in HandoverNote.Priority.values:
        priority = HandoverNote.Priority.NORMAL
    note = HandoverNote.objects.create(
        business_day=day,
        message=message,
        priority=priority,
        assigned_to=assigned_to,
        created_by=actor,
    )
    recipients = [assigned_to] if assigned_to else list(on_duty_users(day))
    recipients = [user for user in recipients if user is not None]
    _notify_users(
        recipients,
        title='ملاحظة تسليم جديدة في هَبّ',
        message='توجد ملاحظة تشغيلية جديدة تحتاج الاطلاع داخل صفحة اليوم التشغيلي.',
        actor=actor,
    )
    _audit(actor, 'business_day.handover_created', business_day_id=day.pk, handover_note_id=note.pk)
    return note


@transaction.atomic
def acknowledge_handover(note, *, actor):
    note = HandoverNote.objects.select_for_update().get(pk=note.pk)
    if note.status == HandoverNote.Status.RESOLVED:
        return note
    note.status = HandoverNote.Status.ACKNOWLEDGED
    note.acknowledged_by = actor
    note.acknowledged_at = timezone.now()
    note.save(update_fields=['status', 'acknowledged_by', 'acknowledged_at', 'updated_at'])
    _audit(actor, 'business_day.handover_acknowledged', handover_note_id=note.pk)
    return note


@transaction.atomic
def resolve_handover(note, *, actor):
    note = HandoverNote.objects.select_for_update().get(pk=note.pk)
    note.status = HandoverNote.Status.RESOLVED
    if note.acknowledged_at is None:
        note.acknowledged_at = timezone.now()
        note.acknowledged_by = actor
    note.resolved_by = actor
    note.resolved_at = timezone.now()
    note.save(update_fields=[
        'status', 'acknowledged_by', 'acknowledged_at', 'resolved_by', 'resolved_at', 'updated_at'
    ])
    _audit(actor, 'business_day.handover_resolved', handover_note_id=note.pk)
    return note


def unresolved_handover_notes(day):
    return HandoverNote.objects.exclude(status=HandoverNote.Status.RESOLVED).filter(
        business_day__business_date__lte=day.business_date,
    ).select_related('business_day', 'assigned_to', 'created_by').order_by('-priority', 'created_at')


def send_daily_code_reminders(day, *, actor=None, min_interval_minutes=None):
    if day.status == BusinessDay.Status.CLOSED or not day.code_version:
        return 0
    interval = int(min_interval_minutes or getattr(settings, 'BUSINESS_DAY_CODE_REMINDER_MINUTES', 120))
    now = timezone.now()
    threshold = now - timedelta(minutes=max(interval, 1))
    users = list(on_duty_users(day))
    reminded = []
    for user in users:
        receipt, _ = StaffDailyCodeReceipt.objects.get_or_create(
            business_day=day,
            user=user,
            defaults={'code_version': day.code_version},
        )
        if receipt.code_version != day.code_version:
            receipt.code_version = day.code_version
            receipt.viewed_at = None
            receipt.first_used_at = None
            receipt.last_used_at = None
            receipt.last_reminded_at = None
            receipt.reminder_count = 0
        if receipt.viewed_at or receipt.first_used_at:
            receipt.save()
            continue
        if receipt.last_reminded_at and receipt.last_reminded_at > threshold:
            receipt.save()
            continue
        receipt.last_reminded_at = now
        receipt.reminder_count += 1
        receipt.save(update_fields=['code_version', 'last_reminded_at', 'reminder_count', 'updated_at'])
        reminded.append(user)
    if reminded:
        _notify_users(
            reminded,
            title=f'تذكير فريق يوم {day.business_date}',
            message='لم يتم تسجيل الاطلاع على رمز اليوم بعد. افتح صفحة اليوم التشغيلي لعرض الرمز وتأكيد الاستلام.',
            actor=actor,
        )
        _audit(actor, 'business_day.staff_code_reminders_sent', business_day_id=day.pk, user_ids=[u.pk for u in reminded])
    return len(reminded)


def maybe_send_opening_reminder(day, *, actor=None):
    if day.status not in {BusinessDay.Status.OPEN, BusinessDay.Status.REOPENED} or day.opening_completed_at:
        return False
    if not day.opened_at:
        return False
    delay = int(getattr(settings, 'BUSINESS_DAY_OPENING_REMINDER_MINUTES', 90))
    now = timezone.now()
    if now < day.opened_at + timedelta(minutes=max(delay, 1)):
        return False
    if day.opening_last_reminded_at and now < day.opening_last_reminded_at + timedelta(minutes=max(delay, 1)):
        return False
    User = get_user_model()
    managers = User.objects.filter(is_active=True).filter(
        models.Q(is_superuser=True) | models.Q(role__in=['admin', 'cashier'])
    ).distinct()
    _notify_users(
        managers,
        title=f'افتتاح يوم {day.business_date} غير مكتمل',
        message='توجد بنود افتتاح مطلوبة لم تُؤكّد بعد. افتح صفحة اليوم التشغيلي لمراجعتها.',
        actor=actor,
    )
    day.opening_last_reminded_at = now
    day.save(update_fields=['opening_last_reminded_at', 'updated_at'])
    return True
