from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.models import InternetEntitlement, InternetPackage, InternetSession, Order, OrderItem
from .catalog import ensure_package_catalog_product
from .network_policy import package_network_backend


@receiver(post_save, sender=InternetPackage, dispatch_uid='internet.sync_package_catalog_product')
def sync_package_catalog_product(sender, instance, raw=False, **kwargs):
    if raw:
        return
    ensure_package_catalog_product(instance)


@receiver(pre_save, sender=InternetEntitlement, dispatch_uid='internet.snapshot_package_network_backend')
def snapshot_package_network_backend(sender, instance, raw=False, **kwargs):
    """Snapshot the package's explicit backend before the entitlement is inserted."""
    if raw or not instance._state.adding or not instance.package_id:
        return
    instance.network_backend = package_network_backend(instance.package)


def _sync_order_basic_wifi(order):
    # Local import keeps signal registration light and avoids coupling app import order.
    from .guest_wifi import sync_order_bonus
    sync_order_bonus(order)


@receiver(post_save, sender=Order, dispatch_uid='internet.sync_basic_wifi_order_status_bonus')
def sync_basic_wifi_order_status_bonus(sender, instance, raw=False, **kwargs):
    if raw:
        return
    _sync_order_basic_wifi(instance)


@receiver(post_save, sender=OrderItem, dispatch_uid='internet.sync_basic_wifi_order_item_bonus')
def sync_basic_wifi_order_item_bonus(sender, instance, raw=False, **kwargs):
    if raw or not instance.order_id:
        return
    _sync_order_basic_wifi(instance.order)


@receiver(post_delete, sender=OrderItem, dispatch_uid='internet.sync_basic_wifi_deleted_item_bonus')
def sync_basic_wifi_deleted_item_bonus(sender, instance, **kwargs):
    if not instance.order_id:
        return
    order = Order.objects.filter(pk=instance.order_id).first()
    if order:
        _sync_order_basic_wifi(order)


@receiver(post_save, sender=InternetSession, dispatch_uid='internet.account_basic_wifi_session_usage')
def account_basic_wifi_session_usage(sender, instance, raw=False, **kwargs):
    if raw or instance.status == InternetSession.Status.ACTIVE:
        return
    from .guest_wifi import account_guest_wifi_session_usage
    account_guest_wifi_session_usage(instance)
