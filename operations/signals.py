from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import NotificationEvent, NotificationRecipient

from .models import BusinessDay, BusinessDayStaffAssignment, StaffDailyCodeReceipt


def _prune_daily_code_delivery_to_roster(business_day_id):
    day = BusinessDay.objects.filter(pk=business_day_id).first()
    if not day:
        return
    assigned_ids = list(day.staff_assignments.values_list('user_id', flat=True))
    if not assigned_ids:
        # Auto-open/no-roster fallback intentionally keeps delivery to active staff.
        return
    day.code_receipts.exclude(user_id__in=assigned_ids).delete()
    NotificationRecipient.objects.filter(
        notification_event__title_ar=f'رمز الفريق ليوم {day.business_date} جاهز',
    ).exclude(user_id__in=assigned_ids).delete()


def _schedule_daily_code_roster_prune(day_id):
    transaction.on_commit(lambda: _prune_daily_code_delivery_to_roster(day_id))


@receiver(pre_save, sender=NotificationEvent, dispatch_uid='operations.scrub_daily_staff_code_notification')
def scrub_daily_staff_code_notification(sender, instance, **kwargs):
    """Prevent the daily staff code from ever being written to notification history."""
    if not (instance.title_ar or '').startswith('رمز الفريق ليوم'):
        return
    instance.message_ar = 'رمز اليوم جاهز. افتح صفحة اليوم التشغيلي لإظهاره بعد تسجيل الدخول.'


@receiver(post_save, sender=BusinessDay, dispatch_uid='operations.seed_business_day_opening_checklist')
def seed_business_day_opening_checklist(sender, instance, created, **kwargs):
    if not created:
        return
    from .opening import seed_opening_checklist
    from .tasks import sync_business_day_tasks

    seed_opening_checklist(instance)
    sync_business_day_tasks(instance)


@receiver(post_save, sender=BusinessDayStaffAssignment, dispatch_uid='operations.prune_daily_code_after_roster_save')
def prune_daily_code_after_roster_save(sender, instance, **kwargs):
    _schedule_daily_code_roster_prune(instance.business_day_id)


@receiver(post_delete, sender=BusinessDayStaffAssignment, dispatch_uid='operations.prune_daily_code_after_roster_delete')
def prune_daily_code_after_roster_delete(sender, instance, **kwargs):
    _schedule_daily_code_roster_prune(instance.business_day_id)


@receiver(post_save, sender=StaffDailyCodeReceipt, dispatch_uid='operations.align_daily_code_receipt_to_roster')
def align_daily_code_receipt_to_roster(sender, instance, **kwargs):
    _schedule_daily_code_roster_prune(instance.business_day_id)


@receiver(post_save, sender=StaffDailyCodeReceipt, dispatch_uid='operations.mark_roster_checkin_from_daily_code')
def mark_roster_checkin_from_daily_code(sender, instance, **kwargs):
    if not instance.first_used_at:
        return
    BusinessDayStaffAssignment.objects.filter(
        business_day=instance.business_day,
        user=instance.user,
        checked_in_at__isnull=True,
    ).update(checked_in_at=timezone.now())
