"""Pure membership-benefit discovery and resolution.

This module deliberately does not create entitlements, orders, payments, or ledger
entries.  Callers receive deterministic values and decide what (if anything) to do.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from django.db.models import Q
from django.utils import timezone

from members.models import MembershipBenefitRule, MembershipSubscription


@dataclass(frozen=True)
class EffectiveBenefit:
    subscription: MembershipSubscription
    definition: dict

    @property
    def benefit_type(self):
        return self.definition.get('benefit_type', '')


@dataclass(frozen=True)
class DiscountResolution:
    benefit: EffectiveBenefit | None
    original_price: Decimal
    discount: Decimal

    @property
    def final_price(self):
        return max(self.original_price - self.discount, Decimal('0'))


def get_active_subscriptions(member, at=None):
    at = at or timezone.now()
    if member is None or not getattr(member, 'is_active', True):
        return []
    candidates = (MembershipSubscription.objects.select_related('plan')
                  .filter(member=member, status__in=(MembershipSubscription.Status.ACTIVE,
                                                     MembershipSubscription.Status.FROZEN),
                          starts_at__lte=at)
                  .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=at))
                  .order_by('starts_at', 'pk'))
    return [subscription for subscription in candidates if subscription.is_active_at(at)]


def _definitions(subscription):
    return subscription.benefit_snapshot


def get_effective_benefits(member, at=None):
    benefits = [
        EffectiveBenefit(subscription, definition)
        for subscription in get_active_subscriptions(member, at)
        for definition in _definitions(subscription)
    ]
    return sorted(benefits, key=lambda benefit: (
        -int(benefit.definition.get('priority') or 0),
        benefit.subscription.plan.code,
        int(benefit.definition.get('rule_id') or 0),
    ))


def _decimal(definition, field='value_decimal'):
    value = definition.get(field)
    return Decimal(str(value)) if value not in (None, '') else Decimal('0')


def _product_scope_matches(definition, product):
    metadata = definition.get('metadata') or {}
    if product.vendor_id and not metadata.get('allow_vendor'):
        return False
    if product.not_discountable or not product.is_available:
        return False

    # Preserve stable relational targets from the pre-generic benefit model.
    relational_fields = ('product_id', 'category_id', 'menu_section_id', 'tag_id')
    if any(definition.get(field) for field in relational_fields):
        if definition.get('product_id') and definition['product_id'] != product.pk:
            return False
        if definition.get('category_id') and definition['category_id'] != product.category_id:
            return False
        if definition.get('menu_section_id') and not product.menu_sections.filter(pk=definition['menu_section_id']).exists():
            return False
        if definition.get('tag_id') and not product.tags.filter(pk=definition['tag_id']).exists():
            return False
    for field in ('item_type', 'beverage_type', 'food_type', 'service_type'):
        if definition.get(field) and definition[field] != getattr(product, field):
            return False
    if product.is_alcoholic and not definition.get('applies_to_alcohol'):
        return False

    scope_type, scope_code = definition.get('scope_type', ''), definition.get('scope_code', '')
    legacy_scoped = any(definition.get(field) for field in relational_fields + (
        'item_type', 'beverage_type', 'food_type', 'service_type'))
    if legacy_scoped:
        return True
    if scope_type == 'all_hub_products':
        return True
    if scope_type == 'beverages':
        return product.item_type == product.ItemType.BEVERAGE
    if scope_type == 'food':
        return product.item_type == product.ItemType.FOOD
    if scope_type == 'product':
        return scope_code in {str(product.pk), str(product.public_code)}
    if scope_type == 'category':
        return scope_code in {str(product.category_id), str(product.category.public_code)}
    return False


def resolve_product_discount(member, product, at=None):
    price = Decimal(product.price_syp)
    candidates = []
    for benefit in get_effective_benefits(member, at):
        definition = benefit.definition
        if not _product_scope_matches(definition, product):
            continue
        if benefit.benefit_type == MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_FIXED:
            amount = _decimal(definition)
        elif benefit.benefit_type == MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_PERCENT:
            amount = (price * _decimal(definition) / Decimal('100')).quantize(Decimal('1'), rounding=ROUND_DOWN)
        else:
            continue
        candidates.append((min(max(amount, Decimal('0')), price), benefit))
    if not candidates:
        return DiscountResolution(None, price, Decimal('0'))
    amount, benefit = sorted(candidates, key=lambda item: (
        -item[0], -int(item[1].definition.get('priority') or 0),
        item[1].subscription.plan.code, int(item[1].definition.get('rule_id') or 0),
    ))[0]
    return DiscountResolution(benefit, price, amount)


def _event_price(event):
    if hasattr(event, 'price_syp'):
        return Decimal(event.price_syp)
    price = event.ticket_types.filter(is_active=True).order_by('price_syp', 'pk').values_list('price_syp', flat=True).first()
    return Decimal(price or 0)


def resolve_event_discount(member, event, at=None):
    price = _event_price(event)
    candidates = []
    for benefit in get_effective_benefits(member, at):
        definition = benefit.definition
        if benefit.benefit_type != MembershipBenefitRule.BenefitType.EVENT_DISCOUNT_PERCENT:
            continue
        scope_type, scope_code = definition.get('scope_type', ''), definition.get('scope_code', '')
        if scope_type == 'event':
            eligible = scope_code in {str(event.pk), str(event.uuid)}
        else:
            eligible = scope_type == 'hub_produced_events' and not event.vendor_participations.exists()
        if eligible:
            amount = (price * _decimal(definition) / Decimal('100')).quantize(Decimal('1'), rounding=ROUND_DOWN)
            candidates.append((min(max(amount, Decimal('0')), price), benefit))
    if not candidates:
        return DiscountResolution(None, price, Decimal('0'))
    amount, benefit = sorted(candidates, key=lambda item: (-item[0], -int(item[1].definition.get('priority') or 0), item[1].subscription.plan.code))[0]
    return DiscountResolution(benefit, price, amount)


def has_booking_priority(member, at=None):
    return any(benefit.benefit_type == MembershipBenefitRule.BenefitType.BOOKING_PRIORITY
               for benefit in get_effective_benefits(member, at))


def get_workspace_allowance(member, at=None):
    return sum(max(int(benefit.definition.get('value_integer') or 0), 0)
               for benefit in get_effective_benefits(member, at)
               if benefit.benefit_type == MembershipBenefitRule.BenefitType.WORKSPACE_MINUTES)


def get_internet_member_pricing_eligibility(member, at=None):
    return any(benefit.benefit_type == MembershipBenefitRule.BenefitType.INTERNET_MEMBER_PRICE
               for benefit in get_effective_benefits(member, at))


def is_member_eligible_for_internet_package(member, package, at=None):
    """Whether a current, effectively-active subscription qualifies for member Internet.

    Eligibility is typed by INTERNET_MEMBER_PRICE or INTERNET_MINUTES (which authorizes
    a membership-credit entitlement), never inferred from a Member row or mutable plan
    name. Scope matching uses the same stable package identities as price resolution.
    """
    if member is None:
        return False
    for benefit in get_internet_benefits(member, at):
        if benefit.benefit_type not in {
                MembershipBenefitRule.BenefitType.INTERNET_MEMBER_PRICE,
                MembershipBenefitRule.BenefitType.INTERNET_MINUTES}:
            continue
        scope = str(benefit.definition.get('scope_code') or '')
        if not scope or scope in {str(package.pk), str(package.public_code), package.code or ''}:
            return True
    return False


def get_internet_benefits(member, at=None):
    types = {MembershipBenefitRule.BenefitType.INTERNET_MINUTES,
             MembershipBenefitRule.BenefitType.INTERNET_MEMBER_PRICE}
    return [benefit for benefit in get_effective_benefits(member, at) if benefit.benefit_type in types]


def resolve_internet_price(member, package, at=None):
    """Resolve one lowest eligible member price; benefits never stack."""
    candidates = []
    for benefit in get_internet_benefits(member, at):
        if benefit.benefit_type != MembershipBenefitRule.BenefitType.INTERNET_MEMBER_PRICE:
            continue
        definition = benefit.definition
        scope = str(definition.get('scope_code') or '')
        if scope and scope not in {str(package.pk), str(package.public_code), package.code or ''}:
            continue
        price = _decimal(definition)
        if price >= 0:
            candidates.append((price, benefit))
    base = Decimal(package.price_syp)
    if not candidates:
        return base, None
    price, benefit = sorted(candidates, key=lambda row: (row[0], -int(row[1].definition.get('priority') or 0), row[1].subscription.pk))[0]
    # Runtime is authoritative even when legacy configuration escaped validation.
    return (price, benefit) if price < base else (base, None)
