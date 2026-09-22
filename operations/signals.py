from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import NotificationEvent


@receiver(post_save, sender=NotificationEvent, dispatch_uid='operations.scrub_daily_staff_code_notification')
def scrub_daily_staff_code_notification(sender, instance, created, **kwargs):
    """Keep the daily code out of persisted notification history.

    Daily-code notifications use the existing non-push daily event route so staff
    land on /staff/close-day/. The code itself remains encrypted on BusinessDay and
    is revealed only after authenticated staff interaction.
    """
    if not created or not (instance.title_ar or '').startswith('رمز الفريق ليوم'):
        return
    safe_message = 'رمز اليوم جاهز. افتح صفحة اليوم التشغيلي لإظهاره بعد تسجيل الدخول.'
    if instance.message_ar != safe_message:
        sender.objects.filter(pk=instance.pk).update(message_ar=safe_message)
        instance.message_ar = safe_message
