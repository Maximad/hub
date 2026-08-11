"""Membership-to-Internet orchestration; routers remain behind the backend API."""
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.models import InternetEntitlement, InternetPartner, InternetRevenueShare
from core.services.internet_access import get_default_internet_partner
from core.services.network_backends import get_network_backend
from members.models import CommercialAllocation, MembershipBenefitRule


@transaction.atomic
def provision_subscription_internet(subscription):
    subscription = type(subscription).objects.select_for_update().select_related('member').get(pk=subscription.pk)
    if not subscription.is_active_at():
        return []
    result = []
    for definition in subscription.benefit_snapshot:
        if definition.get('benefit_type') != MembershipBenefitRule.BenefitType.INTERNET_MINUTES:
            continue
        rule_id = definition.get('rule_id')
        key = f'membership:{subscription.uuid}:internet:{rule_id}'
        existing = InternetEntitlement.objects.filter(idempotency_key=key).first()
        if existing:
            result.append(existing); continue
        minutes = definition.get('value_integer') or definition.get('included_minutes')
        if not minutes or int(minutes) <= 0:
            raise ValidationError('Internet-minute benefit requires a positive allowance.')
        concurrent = int(definition.get('max_concurrent_devices') or 1)
        registered = int(definition.get('max_registered_devices') or concurrent)
        if concurrent > registered:
            raise ValidationError('Concurrent devices cannot exceed registered devices.')
        metadata = definition.get('metadata') or {}
        partner = InternetPartner.objects.filter(pk=metadata.get('partner_id'), active=True).first() if metadata.get('partner_id') else None
        partner = partner or get_default_internet_partner()
        percent = metadata.get('partner_share_percent', partner.revenue_share_percent if partner else None)
        amount = int(definition.get('commercial_allocation_syp') or 0)
        entitlement = InternetEntitlement.objects.create(
            member=subscription.member, subscription=subscription, source_benefit_rule_id=rule_id,
            origin_type='membership_benefit', idempotency_key=key, access_mode='allowance',
            activation_policy=metadata.get('activation_policy', 'on_purchase'), activated_at=timezone.now(),
            valid_from=subscription.starts_at, valid_until=subscription.ends_at,
            total_minutes_allowed=int(minutes), bandwidth_profile_code=definition.get('internet_bandwidth_profile_code') or '',
            max_concurrent_devices=concurrent, max_registered_devices=registered,
            status=InternetEntitlement.Status.ACTIVE, partner=partner,
            partner_name_snapshot=partner.name if partner else '', partner_share_percent_snapshot=percent,
            gross_amount_syp=amount, network_backend=metadata.get('network_backend', 'manual'))
        share_amount = ((Decimal(amount) * Decimal(str(percent)) / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        if partner and percent is not None else None)
        CommercialAllocation.objects.create(
            subscription=subscription, component_type='internet', source_benefit_rule_id=rule_id,
            allocated_amount_syp=amount, internet_entitlement=entitlement, partner=partner,
            partner_name_snapshot=partner.name if partner else '', partner_share_percent=percent,
            partner_share_amount_syp=share_amount, metadata={'benefit_snapshot': definition})
        if share_amount is not None:
            InternetRevenueShare.objects.get_or_create(entitlement=entitlement, defaults={
                'partner': partner, 'subscription': subscription, 'gross_amount_syp': amount,
                'share_percent': percent, 'partner_amount_syp': share_amount,
                'hub_amount_syp': Decimal(amount) - share_amount, 'business_date': timezone.localdate()})
        get_network_backend(entitlement.network_backend).provision_access(entitlement)
        result.append(entitlement)
    allocated = sum(a.allocated_amount_syp for a in subscription.commercial_allocations.all())
    gross = subscription.gross_amount_syp
    if gross is not None and allocated < gross and not subscription.commercial_allocations.filter(component_type='membership').exists():
        CommercialAllocation.objects.create(subscription=subscription, component_type='membership', allocated_amount_syp=gross - allocated)
    return result


@transaction.atomic
def invalidate_subscription_internet(subscription, reason=''):
    for entitlement in subscription.internet_entitlements.exclude(status__in=('cancelled', 'expired')):
        entitlement.status = InternetEntitlement.Status.CANCELLED
        entitlement.cancellation_reason = reason or 'Membership subscription cancelled'
        entitlement.save(update_fields=['status', 'cancellation_reason', 'updated_at'])
        get_network_backend(entitlement.network_backend).disconnect_access(entitlement)
