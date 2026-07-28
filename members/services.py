import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.utils import timezone

from members.models import MemberActivationToken, MemberDeviceToken, MembershipBenefitRule, MembershipSubscription


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class MemberContext:
    member: object
    subscription: MembershipSubscription
    plan: object
    device: MemberDeviceToken | None = None


@dataclass(frozen=True)
class BenefitResult:
    rule: MembershipBenefitRule | None
    original_total: int
    discount: int

    @property
    def final_total(self):
        return max(self.original_total - self.discount, 0)


def get_active_member_context(member, at=None, device=None):
    at = at or timezone.now()
    if not getattr(member, 'is_active', True):
        return None
    subscription = (MembershipSubscription.objects.select_related('plan')
        .filter(member=member, status='active', starts_at__lte=at, plan__is_active=True)
        .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=at))
        .order_by('-starts_at').first())
    return MemberContext(member, subscription, subscription.plan, device) if subscription else None


# Imported here to keep the public API above uncluttered.
from django.db import models  # noqa: E402


def create_activation_token(member, created_by=None):
    raw = secrets.token_urlsafe(32)
    token = MemberActivationToken.objects.create(
        member=member, token_hash=_digest(raw), created_by=created_by,
        expires_at=timezone.now() + timedelta(seconds=settings.MEMBER_ACTIVATION_TOKEN_AGE),
    )
    return token, raw


def consume_activation_token(raw, device_label=''):
    now = timezone.now()
    candidate = MemberActivationToken.objects.select_related('member').filter(token_hash=_digest(raw)).first()
    if not candidate or not hmac.compare_digest(candidate.token_hash, _digest(raw)) or candidate.consumed_at or candidate.revoked_at or candidate.expires_at <= now:
        return None, None
    candidate.consumed_at = now
    candidate.save(update_fields=['consumed_at'])
    secret = secrets.token_urlsafe(32)
    device = MemberDeviceToken.objects.create(
        member=candidate.member, token_hash=_digest(secret), device_label=device_label[:120],
        expires_at=now + timedelta(seconds=settings.MEMBER_DEVICE_COOKIE_AGE),
    )
    return device, f'{device.uuid}.{secret}'


def resolve_member_from_request(request, touch=True):
    raw = request.COOKIES.get(settings.MEMBER_DEVICE_COOKIE_NAME, '')
    try:
        public_id, secret = raw.split('.', 1)
        device = MemberDeviceToken.objects.select_related('member').get(uuid=public_id)
    except (ValueError, MemberDeviceToken.DoesNotExist):
        return None
    now = timezone.now()
    if device.revoked_at or (device.expires_at and device.expires_at <= now) or not hmac.compare_digest(device.token_hash, _digest(secret)):
        return None
    context = get_active_member_context(device.member, now, device)
    if context and touch and (not device.last_used_at or now - device.last_used_at > timedelta(hours=1)):
        MemberDeviceToken.objects.filter(pk=device.pk, revoked_at__isnull=True).update(last_used_at=now)
    return context


def evaluate_membership_benefit(context, product, quantity=1, unit_price=None):
    total = max(int(unit_price if unit_price is not None else product.price_syp), 0) * max(int(quantity), 0)
    if not context or product.not_discountable or not product.is_available:
        return BenefitResult(None, total, 0)
    rules = context.plan.benefit_rules.filter(is_active=True).select_related('product', 'category', 'menu_section', 'tag').order_by('-priority', 'pk')
    section_ids = set(product.menu_sections.values_list('pk', flat=True))
    tag_ids = set(product.tags.values_list('pk', flat=True))
    matches = []
    for rule in rules:
        targets = [(rule.product_id, product.pk), (rule.category_id, product.category_id), (rule.menu_section_id, section_ids), (rule.tag_id, tag_ids)]
        relational = any(value for value, _ in targets)
        ok = ((rule.product_id == product.pk) if rule.product_id else True) and ((rule.category_id == product.category_id) if rule.category_id else True)
        ok = ok and ((rule.menu_section_id in section_ids) if rule.menu_section_id else True) and ((rule.tag_id in tag_ids) if rule.tag_id else True)
        for field in ('item_type', 'beverage_type', 'food_type', 'service_type'):
            wanted = getattr(rule, field)
            if wanted and wanted != getattr(product, field): ok = False
        if product.is_alcoholic and not rule.applies_to_alcohol: ok = False
        if ok: matches.append(rule)
    if not matches: return BenefitResult(None, total, 0)
    # Priority is authoritative; at equal priority prefer the most specific rule, never stack.
    def specificity(r):
        return sum(bool(getattr(r, f)) for f in ('product_id','category_id','menu_section_id','tag_id','item_type','beverage_type','food_type','service_type'))
    rule = sorted(matches, key=lambda r: (-r.priority, -specificity(r), r.pk))[0]
    percent = int(rule.discount_percent or 0)
    discount = int((Decimal(total) * Decimal(percent) / 100).quantize(Decimal('1'), rounding=ROUND_DOWN))
    discount += max(int(rule.discount_amount_syp or 0), 0) * max(int(quantity), 0)
    if rule.included_quantity:
        discount += min(quantity, rule.included_quantity) * max(int(unit_price if unit_price is not None else product.price_syp), 0)
    return BenefitResult(rule, total, min(discount, total))
