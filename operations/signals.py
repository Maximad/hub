from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import NotificationEvent

from .models import BusinessDay, BusinessDayStaffAssignment, StaffDailyCodeReceipt


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
    seed_opening_checklist(instance)


@receiver(post_save, sender=StaffDailyCodeReceipt, dispatch_uid='operations.mark_roster_checkin_from_daily_code')
def mark_roster_checkin_from_daily_code(sender, instance, **kwargs):
    if not instance.first_used_at:
        return
    BusinessDayStaffAssignment.objects.filter(
        business_day=instance.business_day,
        user=instance.user,
        checked_in_at__isnull=True,
    ).update(checked_in_at=timezone.now())
