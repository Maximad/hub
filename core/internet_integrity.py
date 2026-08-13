"""Read-only commercial and access integrity diagnostics for paid Internet."""
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Lower

from core.models import (InternetAccessDevice, InternetEntitlement,
                         InternetRevenueShare, InternetSession)
from members.models import CommercialAllocation, MembershipSubscription


def commercial_integrity_findings():
    findings = []
    for subscription in MembershipSubscription.objects.filter(
            commercial_allocations__isnull=False).distinct().annotate(
            allocation_total=Sum('commercial_allocations__allocated_amount_syp')):
        gross = subscription.gross_amount_syp
        total = subscription.allocation_total or 0
        if gross is not None and total != gross:
            findings.append({'code': 'allocation_total', 'subscription_id': subscription.pk,
                             'gross': str(gross), 'allocation_total': str(total),
                             'difference': str(Decimal(gross) - Decimal(total))})

    allocations = CommercialAllocation.objects.filter(
        component_type=CommercialAllocation.ComponentType.INTERNET,
        internet_entitlement__isnull=False).select_related(
            'internet_entitlement', 'partner', 'subscription')
    for allocation in allocations:
        entitlement = allocation.internet_entitlement
        share = InternetRevenueShare.objects.filter(entitlement=entitlement).first()
        mismatch = (entitlement.subscription_id != allocation.subscription_id or
                    Decimal(entitlement.gross_amount_syp) != Decimal(allocation.allocated_amount_syp) or
                    entitlement.partner_id != allocation.partner_id or
                    entitlement.partner_name_snapshot != allocation.partner_name_snapshot or
                    entitlement.partner_share_percent_snapshot != allocation.partner_share_percent)
        if share:
            mismatch = mismatch or any((
                share.subscription_id != allocation.subscription_id,
                share.partner_id != allocation.partner_id,
                share.partner_name_snapshot != allocation.partner_name_snapshot,
                share.share_percent != allocation.partner_share_percent,
                share.gross_amount_syp != allocation.allocated_amount_syp,
                share.partner_amount_syp != allocation.partner_share_amount_syp,
                share.partner_amount_syp + share.hub_amount_syp != share.gross_amount_syp,
            ))
        elif allocation.partner_id and allocation.partner_share_percent is not None:
            mismatch = True
        if mismatch:
            findings.append({'code': 'commercial_snapshot_mismatch',
                             'subscription_id': allocation.subscription_id,
                             'entitlement_id': entitlement.pk,
                             'allocation_id': allocation.pk,
                             'revenue_share_id': share.pk if share else None})

    for share in InternetRevenueShare.objects.annotate(
            gross_delta=Sum('adjustments__gross_delta_syp'),
            partner_delta=Sum('adjustments__partner_delta_syp'),
            hub_delta=Sum('adjustments__hub_delta_syp')):
        deltas = (share.gross_delta or 0, share.partner_delta or 0, share.hub_delta or 0)
        originals = (share.gross_amount_syp, share.partner_amount_syp, share.hub_amount_syp)
        if any(delta < -original for delta, original in zip(deltas, originals)):
            findings.append({'code': 'reversal_exceeds_original', 'revenue_share_id': share.pk})
    return findings


def access_integrity_findings():
    findings = []
    duplicates = (InternetAccessDevice.objects.values('entitlement_id', mac=Lower('device_mac'))
                  .annotate(count=Count('id')).filter(count__gt=1))
    for row in duplicates:
        findings.append({'code': 'duplicate_mac', **row})
    for entitlement in InternetEntitlement.objects.annotate(
            active_count=Count('sessions', filter=Q(
                sessions__status=InternetSession.Status.ACTIVE)),
            reserved=Sum('sessions__reserved_minutes', filter=Q(
                sessions__status=InternetSession.Status.ACTIVE))):
        if entitlement.active_count > entitlement.max_concurrent_devices:
            findings.append({'code': 'concurrent_sessions', 'entitlement_id': entitlement.pk})
        if (entitlement.total_minutes_allowed is not None and
                entitlement.minutes_used + (entitlement.reserved or 0) > entitlement.total_minutes_allowed):
            findings.append({'code': 'allowance_exceeded', 'entitlement_id': entitlement.pk})
    return findings
