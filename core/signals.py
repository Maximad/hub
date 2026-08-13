"""Small integration hooks around persisted domain transitions."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Payment


@receiver(post_save, sender=Payment, dispatch_uid='internet-payment-reversal-lifecycle')
def payment_reversal_lifecycle(sender, instance, **kwargs):
    """Join the authoritative finance transaction; ordinary payments are a no-op."""
    if not instance.is_reversed:
        return
    from core.services.internet_lifecycle import apply_payment_reversal
    apply_payment_reversal(instance, actor=instance.reversed_by)
