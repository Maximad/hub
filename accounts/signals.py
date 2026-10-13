"""Schedule staff Internet revocation after a disabled staff account is committed.

The immediate captive-portal check already denies inactive users. The durable
disconnect is queued only after commit so an uncommitted account change does
not revoke network access.
"""
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid='hub-staff-wifi-deactivation')
def schedule_disabled_staff_internet_revocation(sender, instance, **kwargs):
    if instance.is_active or instance.pk is None:
        return

    # The reconciliation command also catches QuerySet.update(), imports,
    # missed hooks, and disabled users from an older deployment.
    user_id = instance.pk

    def reconcile():
        from core.services.staff_internet_lifecycle import revoke_disabled_staff_internet
        revoke_disabled_staff_internet(user_id=user_id, limit=100)

    transaction.on_commit(reconcile)
