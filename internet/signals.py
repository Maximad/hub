from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import InternetPackage
from .catalog import ensure_package_catalog_product


@receiver(post_save, sender=InternetPackage, dispatch_uid='internet.sync_package_catalog_product')
def sync_package_catalog_product(sender, instance, raw=False, **kwargs):
    if raw:
        return
    ensure_package_catalog_product(instance)
