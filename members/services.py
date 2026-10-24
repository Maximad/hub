import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from members.models import MemberActivationToken, MemberDeviceToken, MembershipSubscription


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class MemberContext:
    member: object
    subscription: MembershipSubscription | None
    plan: object | None
    device: MemberDeviceToken | None = None


@dataclass(frozen=True)
class BenefitResult:
    benefit: object | None
    original_total: int
    discount: int

    @property
    def final_total(self):
        return max(self.original_total - self.discount, 0)


def get_active_member_context(member, at=None, device=None):
    """Select a display subscription using the same effective-status policy as benefits."""
    at = at or timezone.now()
    if member is None or not getattr(member, 'is_active', True):
        return None
    subscriptions = get_active_subscriptions(member, at)
    subscription = max(subscriptions, key=lambda item: (item.starts_at, item.pk), default=None)
    return MemberContext(member, subscription, subscription.plan, device) if subscription else None


def get_member_context(member, at=None, device=None):
    """Return permanent member identity plus an active subscription when one exists."""
    at = at or timezone.now()
    if not getattr(member, 'is_active', True):
        return None
    active = get_active_member_context(member, at, device)
    return active or MemberContext(member=member, subscription=None, plan=None, device=device)


def create_activation_token(member, created_by=None):
    raw = secrets.token_urlsafe(32)
    token = MemberActivationToken.objects.create(
        member=member, token_hash=_digest(raw), created_by=created_by,
        expires_at=timezone.now() + timedelta(seconds=settings.MEMBER_ACTIVATION_TOKEN_AGE),
    )
    return token, raw


def validate_activation_token(raw, at=None):
    """Return non-secret activation metadata without changing token state."""
    now = at or timezone.now()
    digest = _digest(raw)
    candidate = MemberActivationToken.objects.select_related('member').filter(token_hash=digest).first()
    if (
        not candidate
        or not hmac.compare_digest(candidate.token_hash, digest)
        or candidate.consumed_at
        or candidate.revoked_at
        or candidate.expires_at <= now
    ):
        return None
    return candidate


@transaction.atomic
def consume_activation_token(raw, device_label=''):
    now = timezone.now()
    candidate = MemberActivationToken.objects.select_for_update().select_related('member').filter(token_hash=_digest(raw)).first()
    if not candidate or not hmac.compare_digest(candidate.token_hash, _digest(raw)) or candidate.consumed_at or candidate.revoked_at or candidate.expires_at <= now:
        return None, None

    # Legacy activation links remain supported, but they now enter the same
    # permanent account authority as invitation-based onboarding.
    from member_accounts.models import MemberAccount
    account, _ = MemberAccount.objects.get_or_create(member=candidate.member)
    if account.status == MemberAccount.Status.LOCKED:
        return None, None

    candidate.consumed_at = now
    candidate.save(update_fields=['consumed_at'])
    secret = secrets.token_urlsafe(32)
    device = MemberDeviceToken.objects.create(
        member=candidate.member, token_hash=_digest(secret), device_label=device_label[:120],
        expires_at=now + timedelta(seconds=settings.MEMBER_DEVICE_COOKIE_AGE),
    )
    if not account.is_claimed:
        account.mark_claimed(now)
    return device, f'{device.uuid}.{secret}'


def resolve_member_from_request(request, touch=True):
    """Resolve a trusted member device even when the current subscription has expired."""
    raw = request.COOKIES.get(settings.MEMBER_DEVICE_COOKIE_NAME, '')
    try:
        public_id, secret = raw.split('.', 1)
        device = MemberDeviceToken.objects.select_related('member').get(uuid=public_id)
    except (ValueError, MemberDeviceToken.DoesNotExist):
        return None
    now = timezone.now()
    if device.revoked_at or (device.expires_at and device.expires_at <= now) or not hmac.compare_digest(device.token_hash, _digest(secret)):
        return None

    from member_accounts.models import MemberAccount
    account, _ = MemberAccount.objects.get_or_create(member=device.member)
    if account.status == MemberAccount.Status.LOCKED:
        return None
    if not account.is_claimed:
        account.mark_claimed(now)

    context = get_member_context(device.member, now, device)
    if context and touch and (not device.last_used_at or now - device.last_used_at > timedelta(hours=1)):
        MemberDeviceToken.objects.filter(pk=device.pk, revoked_at__isnull=True).update(last_used_at=now)
    return context


def evaluate_membership_benefit(context, product, quantity=1, unit_price=None):
    """Compatibility adapter for menu/POS, backed by immutable subscription benefits."""
    resolution = resolve_product_discount(
        context.member if context else None, product,
        quantity=quantity, unit_price=unit_price)
    return BenefitResult(resolution.benefit, int(resolution.original_price), int(resolution.discount))


# All member-facing calculations use the snapshot-based benefit API below.
from members.benefits import (  # noqa: E402,F401
    get_active_subscriptions, get_effective_benefits, get_internet_benefits,
    get_internet_member_pricing_eligibility, get_workspace_allowance,
    has_booking_priority, resolve_event_discount, resolve_product_discount,
)
