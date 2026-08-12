"""Atomic membership Internet commercial provisioning (network I/O is outboxed)."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (InternetBandwidthProfile, InternetEntitlement,
                         InternetNetworkOperation, InternetPartner,
                         InternetRevenueShare)
from core.services.internet_access import get_default_internet_partner
from core.services.network_operations import enqueue_network_operation
from members.models import CommercialAllocation, MembershipBenefitRule


@dataclass(frozen=True)
class PreparedBenefit:
    snapshot: dict
    rule_id: int
    minutes: int
    concurrent: int
    registered: int
    allocation: int
    partner: object
    percent: object
    exempt: bool
    policy: str
    backend: str


def _decimal_percent(value):
    try:
        percent = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError('Partner share percentage is invalid.') from exc
    if percent < 0 or percent > 100:
        raise ValidationError('Partner share percentage must be between 0 and 100.')
    return percent


def _prepare(subscription, definition):
    rule_id = definition.get('rule_id')
    if not rule_id:
        raise ValidationError('Internet benefit snapshot has no source rule.')
    minutes = definition.get('value_integer') or definition.get('included_minutes')
    if not minutes or int(minutes) <= 0:
        raise ValidationError('Internet-minute benefit requires a positive allowance.')
    concurrent = int(definition.get('max_concurrent_devices') or 1)
    registered = int(definition.get('max_registered_devices') or concurrent)
    if concurrent < 1 or registered < 1 or concurrent > registered:
        raise ValidationError('Internet benefit device limits are invalid.')
    profile_code = definition.get('internet_bandwidth_profile_code') or ''
    if profile_code and not InternetBandwidthProfile.objects.filter(
            code=profile_code, is_active=True).exists():
        raise ValidationError('Internet benefit bandwidth profile is missing or inactive.')
    metadata = definition.get('metadata') or {}
    explicit_partner = 'partner_id' in metadata and metadata.get('partner_id') not in (None, '')
    if explicit_partner:
        partner = InternetPartner.objects.filter(pk=metadata['partner_id'], active=True).first()
        if partner is None:
            raise ValidationError('Explicit Internet partner is missing or inactive.')
    else:
        partner = get_default_internet_partner()
    raw_percent = metadata.get('partner_share_percent')
    percent = _decimal_percent(raw_percent if raw_percent is not None else partner.revenue_share_percent) if partner else None
    allocation = definition.get('commercial_allocation_syp')
    allocation = 0 if allocation is None else int(allocation)
    if allocation < 0:
        raise ValidationError('Internet commercial allocation cannot be negative.')
    exempt = bool(definition.get('complimentary_partner_service', False))
    if partner and percent and allocation == 0 and not exempt:
        raise ValidationError('A paid partner Internet benefit requires a positive commercial allocation or explicit complimentary exemption.')
    if exempt and allocation != 0:
        raise ValidationError('Complimentary partner-service exemption requires a zero allocation.')
    policy = metadata.get('activation_policy', 'on_purchase')
    if policy not in ('on_purchase', 'on_first_use'):
        raise ValidationError('Membership Internet activation policy is unsupported.')
    return PreparedBenefit(definition, int(rule_id), int(minutes), concurrent, registered,
                           allocation, partner, percent, exempt, policy,
                           metadata.get('network_backend', 'manual'))


@transaction.atomic
def provision_subscription_internet(subscription):
    subscription = type(subscription).objects.select_for_update().select_related('member').get(pk=subscription.pk)
    if not subscription.is_active_at():
        return []
    definitions = [d for d in subscription.benefit_snapshot
                   if d.get('benefit_type') == MembershipBenefitRule.BenefitType.INTERNET_MINUTES]
    prepared = [_prepare(subscription, definition) for definition in definitions]
    if not prepared:
        return []
    if subscription.gross_amount_syp is None:
        raise ValidationError('Membership gross amount is required for Internet allocation.')
    total = sum(item.allocation for item in prepared)
    gross = int(subscription.gross_amount_syp)
    if total > gross:
        raise ValidationError('Internet allocations exceed membership gross amount.')

    result = []
    for item in prepared:
        key = f'membership:{subscription.uuid}:internet:{item.rule_id}'
        active = item.policy == 'on_purchase'
        defaults = dict(
            member=subscription.member, subscription=subscription,
            source_benefit_rule_id=item.rule_id, origin_type='membership_benefit',
            access_mode='allowance', activation_policy=item.policy,
            activated_at=subscription.activated_at if active else None,
            valid_from=subscription.starts_at, valid_until=subscription.ends_at,
            total_minutes_allowed=item.minutes,
            bandwidth_profile_code=item.snapshot.get('internet_bandwidth_profile_code') or '',
            max_concurrent_devices=item.concurrent, max_registered_devices=item.registered,
            status=InternetEntitlement.Status.ACTIVE if active else InternetEntitlement.Status.PENDING,
            partner=item.partner, partner_name_snapshot=item.partner.name if item.partner else '',
            partner_share_percent_snapshot=None if item.exempt else item.percent,
            gross_amount_syp=item.allocation, network_backend=item.backend)
        entitlement, created = InternetEntitlement.objects.get_or_create(
            idempotency_key=key, defaults=defaults)
        if not created and (entitlement.subscription_id != subscription.pk or
                            entitlement.source_benefit_rule_id != item.rule_id):
            raise ValidationError('Existing Internet entitlement conflicts with benefit snapshot.')
        share_amount = None
        if item.partner and item.percent is not None and not item.exempt:
            share_amount = (Decimal(item.allocation) * item.percent / Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP)
        allocation, allocation_created = CommercialAllocation.objects.get_or_create(
            subscription=subscription, component_type=CommercialAllocation.ComponentType.INTERNET,
            source_benefit_rule_id=item.rule_id,
            defaults={'allocated_amount_syp': item.allocation,
                      'internet_entitlement': entitlement, 'partner': item.partner,
                      'partner_name_snapshot': item.partner.name if item.partner else '',
                      'partner_share_percent': None if item.exempt else item.percent,
                      'partner_share_amount_syp': share_amount,
                      'metadata': {'benefit_snapshot': item.snapshot,
                                   'complimentary_partner_service': item.exempt}})
        if not allocation_created and (allocation.internet_entitlement_id != entitlement.pk or
                                       allocation.allocated_amount_syp != item.allocation):
            raise ValidationError('Existing Internet allocation conflicts with benefit snapshot.')
        if share_amount is not None:
            InternetRevenueShare.objects.get_or_create(entitlement=entitlement, defaults={
                'partner': item.partner, 'subscription': subscription,
                'gross_amount_syp': item.allocation, 'share_percent': item.percent,
                'partner_amount_syp': share_amount,
                'hub_amount_syp': Decimal(item.allocation) - share_amount,
                'business_date': timezone.localdate()})
        if active:
            enqueue_network_operation(entitlement, InternetNetworkOperation.Operation.PROVISION)
        result.append(entitlement)

    residual = gross - total
    CommercialAllocation.objects.get_or_create(
        subscription=subscription, component_type=CommercialAllocation.ComponentType.MEMBERSHIP,
        source_benefit_rule_id=None,
        defaults={'allocated_amount_syp': residual,
                  'metadata': {'allocation_basis': 'membership_gross_residual'}})
    return result


@transaction.atomic
def invalidate_subscription_internet(subscription, reason=''):
    """Preserve current cancellation semantics while making disconnect durable."""
    for entitlement in subscription.internet_entitlements.exclude(status__in=('cancelled', 'expired')):
        entitlement.status = InternetEntitlement.Status.CANCELLED
        entitlement.cancellation_reason = reason or 'Membership subscription cancelled'
        entitlement.save(update_fields=['status', 'cancellation_reason', 'updated_at'])
        enqueue_network_operation(
            entitlement, InternetNetworkOperation.Operation.DISCONNECT,
            reason=reason, idempotency_key=f'entitlement:{entitlement.public_code}:disconnect:membership-cancelled')
