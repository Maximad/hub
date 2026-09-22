from django.db.models.signals import pre_save
from django.dispatch import receiver

from core.models import NotificationEvent


@receiver(pre_save, sender=NotificationEvent, dispatch_uid='operations.scrub_daily_staff_code_notification')
def scrub_daily_staff_code_notification(sender, instance, **kwargs):
    """Prevent the daily staff code from ever being written to notification history.

    The code itself remains encrypted on BusinessDay and is revealed only after
    authenticated staff interaction. Notifications may say that the code is ready,
    but must never persist the six-digit secret in plaintext.
    """
    if not (instance.title_ar or '').startswith('رمز الفريق ليوم'):
        return
    instance.message_ar = 'رمز اليوم جاهز. افتح صفحة اليوم التشغيلي لإظهاره بعد تسجيل الدخول.'
