"""Small integration hooks around persisted domain transitions."""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models import Payment, Product


def _uses_dedicated_catalog_workflow(product):
    """Return True for products whose presentation is owned outside menu/POS."""
    return (
        product.product_type in {Product.ProductType.INTERNET, Product.ProductType.MEMBERSHIP}
        or product.service_type == Product.ServiceType.INTERNET
        or product.item_type == Product.ItemType.MEMBERSHIP
    )


@receiver(pre_save, sender=Product, dispatch_uid='sync-product-legacy-channel-flags')
def sync_product_legacy_channel_flags(sender, instance, **kwargs):
    """Keep deprecated POS flags aligned with the canonical English menu fields.

    ``visible_on_qr`` and ``orderable_on_qr`` now control both the public menu
    and Staff POS. The old POS-specific columns remain temporarily for schema
    compatibility, so ordinary product saves mirror the canonical values into
    them. Internet and membership identities keep their dedicated workflows.
    """
    if _uses_dedicated_catalog_workflow(instance):
        return
    instance.visible_on_pos = instance.visible_on_qr
    instance.orderable_on_pos = instance.orderable_on_qr


@receiver(post_save, sender=Payment, dispatch_uid='internet-payment-reversal-lifecycle')
def payment_reversal_lifecycle(sender, instance, **kwargs):
    """Join the authoritative finance transaction; ordinary payments are a no-op."""
    if not instance.is_reversed:
        return
    from core.services.internet_lifecycle import apply_payment_reversal
    apply_payment_reversal(instance, actor=instance.reversed_by)
