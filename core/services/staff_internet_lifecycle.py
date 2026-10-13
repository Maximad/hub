"""Bounded, idempotent revocation of grants belonging to inactive Hub staff.

This service changes Django entitlements only. Existing durable network outbox
handles disconnection of Hub-owned HotSpot users, without router setup changes.
"""
from django.db import transaction

from core.models import ActivityLog, InternetEntitlement
from core.services.internet_lifecycle import cancel_internet_entitlement
from internet.models import InternalStaffInternetGrant
from core.services.internet_internal_access import INTERNAL_ORIGINS


REVOCABLE = (
    InternetEntitlement.Status.PENDING,
    InternetEntitlement.Status.ACTIVE,
    InternetEntitlement.Status.SUSPENDED,
)


def disabled_staff_internet_grant_ids(*, user_id=None, limit=200):
    if not 1 <= int(limit) <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    qs = InternalStaffInternetGrant.objects.filter(
        user__is_active=False,
        entitlement__origin_type__in=INTERNAL_ORIGINS,
        entitlement__status__in=REVOCABLE,
    )
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    return list(qs.order_by('entitlement_id').values_list('entitlement_id', flat=True)[:limit])


def revoke_disabled_staff_internet(*, user_id=None, limit=200):
    revoked = 0
    for entitlement_id in disabled_staff_internet_grant_ids(user_id=user_id, limit=limit):
        with transaction.atomic():
            target = (
                InternalStaffInternetGrant.objects.select_related('user')
                .select_for_update()
                .filter(entitlement_id=entitlement_id, user__is_active=False)
                .first()
            )
            if target is None:
                continue
            entitlement = (
                InternetEntitlement.objects.select_for_update()
                .filter(pk=entitlement_id, origin_type__in=INTERNAL_ORIGINS,
                        status__in=REVOCABLE)
                .first()
            )
            if entitlement is None:
                continue
            cancel_internet_entitlement(
                entitlement, actor=None, reason='staff_account_deactivated',
            )
            ActivityLog.objects.create(
                action='internet.internal_staff_grant_revoked_on_deactivation',
                details={
                    'entitlement_id': entitlement.pk,
                    'staff_user_id': target.user_id,
                    'reason': 'staff_account_deactivated',
                },
            )
            revoked += 1
    return revoked
