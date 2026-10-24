"""Guarded activation for already-committed membership sales.

Only sales created through the authoritative membership checkout may be activated
here. It neither creates payments nor modifies the original order or sale.
"""
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, Payment
from members.models import MembershipSubscription


@dataclass(frozen=True)
class ActivationAttempt:
    status: str
    error: str = ''


@transaction.atomic
def _activate_verified_sale(subscription_id, *, at=None, actor_id=None):
    at = at or timezone.now()
    # Nullable payment/order joins must not be locked by FOR UPDATE on PostgreSQL.
    subscription = (MembershipSubscription.objects.select_for_update(of=('self',))
                    .select_related('member')
                    .get(pk=subscription_id))
    if subscription.status == MembershipSubscription.Status.ACTIVE:
        return 'already_active'
    if subscription.status != MembershipSubscription.Status.PENDING:
        return 'not_pending'
    if subscription.starts_at > at:
        return 'not_due'
    if subscription.ends_at is not None and subscription.ends_at <= at:
        return 'out_of_term'
    if not subscription.member.is_active:
        raise ValidationError('Membership holder is inactive.')

    # Never automatically activate legacy/manual grants with no commercial trail.
    if not subscription.sale_idempotency_key or subscription.order_id is None:
        raise ValidationError('Only an authoritative membership sale may be recovered.')
    gross = subscription.gross_amount_syp
    if gross is None or subscription.order.member_id != subscription.member_id:
        raise ValidationError('Membership sale provenance is incomplete.')
    if subscription.order.total_syp != gross:
        raise ValidationError('Membership sale total does not match its order.')
    if gross == 0:
        if not subscription.is_complimentary or subscription.payment_id is not None:
            raise ValidationError('Complimentary membership provenance is invalid.')
    else:
        payment = subscription.payment
        if (subscription.is_complimentary or payment is None
                or payment.order_id != subscription.order_id
                or payment.amount_syp != gross
                or payment.method not in (Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER)
                or not payment.is_active or payment.is_reversed):
            raise ValidationError('Membership payment is missing, reversed, or inconsistent.')

    # activate() is atomic, including benefit allocation / entitlement creation.
    # A provisioning error rolls back the entire membership activation.
    subscription.activate(at=at)
    MembershipSubscription.objects.filter(pk=subscription.pk).update(activation_error='')
    ActivityLog.objects.create(
        actor_id=actor_id, action='membership.sale_activated',
        details={'subscription_id': str(subscription.uuid), 'source': 'guarded_activation'})
    return 'activated'


def attempt_membership_sale_activation(subscription_id, *, actor_id=None, at=None):
    """Return a result and preserve recoverable errors outside the failed transaction."""
    try:
        status = _activate_verified_sale(subscription_id, at=at, actor_id=actor_id)
    except Exception as exc:
        error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        error = (error or 'Membership activation failed.')[:1000]
        # Do not regress an activation completed by another worker concurrently.
        changed = MembershipSubscription.objects.filter(
            pk=subscription_id, status=MembershipSubscription.Status.PENDING
        ).update(activation_error=error)
        if changed:
            details = {'subscription_id': subscription_id, 'error': error[:500]}
            ActivityLog.objects.create(
                actor_id=actor_id, action='membership.internet_activation_failed', details=details)
            ActivityLog.objects.create(
                actor_id=actor_id, action='membership.sale_activation_failed', details=details)
        return ActivationAttempt('failed', error)
    return ActivationAttempt(status)
