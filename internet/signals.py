from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models import InternetEntitlement, InternetPackage
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
