"""Authoritative commercial Internet lifecycle orchestration.

Every public transition locks its aggregate, settles sessions, and writes a durable
network target in the same transaction.  It never calls a network backend.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (ActivityLog, InternetEntitlement, InternetNetworkOperation,
                         InternetRevenueShare, InternetSession)
from core.services.internet_access import (end_usage_session,
                                            record_payment_reversal_adjustment)
from core.services.network_operations import enqueue_network_operation


def _audit(actor, action, **details):
    ActivityLog.objects.create(actor=actor, action=action, details=details)


@transaction.atomic
def terminate_active_sessions(entitlement, *, reason, at=None, actor=None):
    """Settle all active sessions at the true authorization/lifecycle boundary."""
    boundary = at or timezone.now()
    ended = 0
    sessions = InternetSession.objects.select_for_update().filter(
        entitlement=entitlement, status=InternetSession.Status.ACTIVE).order_by('pk')
    for session in sessions:
        session = end_usage_session(session, actor=actor, at=boundary)
        session.lifecycle_end_reason = reason[:200]
        session.save(update_fields=('lifecycle_end_reason', 'updated_at'))
        _audit(actor, 'internet.session_lifecycle_ended', session_id=session.pk,
               entitlement_id=entitlement.pk, reason=reason, boundary=boundary.isoformat())
        ended += 1
    return ended


def _network(entitlement, operation, reason, key):
    job = enqueue_network_operation(entitlement, operation, reason=reason,
                                    idempotency_key=key)
    _audit(None, 'internet.network_operation_enqueued', entitlement_id=entitlement.pk,
           operation=operation, reason=reason, operation_id=job.pk)
    return job


@transaction.atomic
def cancel_internet_entitlement(entitlement, *, actor=None, reason, effective_at=None):
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.status == entitlement.Status.CANCELLED:
        return entitlement
    if entitlement.status == entitlement.Status.EXPIRED:
        return entitlement
    boundary = effective_at or timezone.now()
    terminate_active_sessions(entitlement, reason=reason, at=boundary, actor=actor)
    entitlement.status = entitlement.Status.CANCELLED
    entitlement.cancelled_by = actor
    entitlement.cancellation_reason = reason
    entitlement.lifecycle_reason = reason[:200]
    entitlement.save(update_fields=('status', 'cancelled_by', 'cancellation_reason',
                                    'lifecycle_reason', 'updated_at'))
    _network(entitlement, InternetNetworkOperation.Operation.DISCONNECT, reason,
             f'entitlement:{entitlement.public_code}:disconnect:cancelled')
    _audit(actor, 'internet.entitlement_cancelled', entitlement_id=entitlement.pk,
           reason=reason, boundary=boundary.isoformat())
    return entitlement


@transaction.atomic
def expire_internet_entitlement(entitlement, *, actor=None, reason='validity_expired', effective_at=None):
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.status in {entitlement.Status.EXPIRED, entitlement.Status.CANCELLED}:
        return entitlement
    boundary = effective_at or entitlement.valid_until
    if boundary is None:
        raise ValidationError('An entitlement without a validity boundary cannot expire.')
    terminate_active_sessions(entitlement, reason=reason, at=boundary, actor=actor)
    entitlement.status = entitlement.Status.EXPIRED
    entitlement.lifecycle_reason = reason[:200]
    entitlement.save(update_fields=('status', 'lifecycle_reason', 'updated_at'))
    _network(entitlement, InternetNetworkOperation.Operation.EXPIRE, reason,
             f'entitlement:{entitlement.public_code}:expire')
    _audit(actor, 'internet.entitlement_expired', entitlement_id=entitlement.pk,
           reason=reason, boundary=boundary.isoformat())
    return entitlement


@transaction.atomic
def freeze_membership(subscription, *, until=None, at=None, actor=None):
    from members.models import MembershipSubscription
    subscription = MembershipSubscription.objects.select_for_update().get(pk=subscription.pk)
    at = at or timezone.now()
    if subscription.status != subscription.Status.ACTIVE:
        raise ValidationError('Only an active subscription can be frozen.')
    if until is not None and until <= at:
        raise ValidationError({'freeze_until': 'Freeze end must be after freeze start.'})
    subscription.status = subscription.Status.FROZEN
    subscription.frozen_at = at
    subscription.freeze_until = until
    subscription.save(update_fields=('status', 'frozen_at', 'freeze_until', 'updated_at'))
    for ent in subscription.internet_entitlements.select_for_update().filter(
            status__in=(InternetEntitlement.Status.ACTIVE, InternetEntitlement.Status.PENDING)):
        terminate_active_sessions(ent, reason='membership_frozen', at=at, actor=actor)
        ent.status = ent.Status.SUSPENDED
        ent.lifecycle_reason = 'membership_frozen'
        ent.save(update_fields=('status', 'lifecycle_reason', 'updated_at'))
        _network(ent, InternetNetworkOperation.Operation.DISCONNECT, 'membership_frozen',
                 f'entitlement:{ent.public_code}:disconnect:freeze:{at.isoformat()}')
    _audit(actor, 'membership.frozen', subscription_id=subscription.pk,
           frozen_at=at.isoformat(), freeze_until=until.isoformat() if until else None)
    return subscription


@transaction.atomic
def unfreeze_membership(subscription, *, at=None, actor=None):
    from members.models import MembershipSubscription
    subscription = MembershipSubscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status != subscription.Status.FROZEN:
        return subscription
    at = at or timezone.now()
    frozen_at = subscription.frozen_at
    if frozen_at is None:
        raise ValidationError('Frozen subscription has no freeze start.')
    seconds = max(int((at - frozen_at).total_seconds()), 0)
    duration = timedelta(seconds=seconds)
    if subscription.ends_at is not None:
        subscription.ends_at += duration
    subscription.total_frozen_duration_seconds += seconds
    subscription.status = subscription.Status.ACTIVE
    subscription.frozen_at = None
    subscription.freeze_until = None
    subscription.save(update_fields=('status', 'frozen_at', 'freeze_until', 'ends_at',
                                     'total_frozen_duration_seconds', 'updated_at'))
    for ent in subscription.internet_entitlements.select_for_update().filter(
            status=InternetEntitlement.Status.SUSPENDED):
        if ent.valid_until is not None:
            ent.valid_until += duration
        ent.status = ent.Status.ACTIVE if ent.activated_at else ent.Status.PENDING
        ent.lifecycle_reason = ''
        ent.save(update_fields=('status', 'valid_until', 'lifecycle_reason', 'updated_at'))
        _network(ent, InternetNetworkOperation.Operation.REFRESH, 'membership_unfrozen',
                 f'entitlement:{ent.public_code}:refresh:thaw:{subscription.total_frozen_duration_seconds}')
    _audit(actor, 'membership.unfrozen', subscription_id=subscription.pk,
           frozen_seconds=seconds, thawed_at=at.isoformat())
    return subscription


@transaction.atomic
def cancel_membership(subscription, *, reason, at=None, actor=None):
    from members.models import MembershipSubscription
    subscription = MembershipSubscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status == subscription.Status.CANCELLED:
        return subscription
    boundary = at or timezone.now()
    if subscription.status == subscription.Status.EXPIRED:
        return subscription
    for ent in subscription.internet_entitlements.select_for_update().exclude(
            status__in=(InternetEntitlement.Status.CANCELLED, InternetEntitlement.Status.EXPIRED)):
        cancel_internet_entitlement(ent, actor=actor, reason=reason or 'membership_cancelled',
                                    effective_at=boundary)
    subscription.status = subscription.Status.CANCELLED
    subscription.cancelled_at = boundary
    subscription.cancellation_reason = reason
    subscription.freeze_until = None  # cancellation wins; no later automatic thaw
    subscription.save(update_fields=('status', 'cancelled_at', 'cancellation_reason',
                                     'freeze_until', 'updated_at'))
    _audit(actor, 'membership.cancelled', subscription_id=subscription.pk,
           reason=reason, boundary=boundary.isoformat())
    return subscription


@transaction.atomic
def expire_membership(subscription, *, at=None):
    from members.models import MembershipSubscription
    subscription = MembershipSubscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status in {subscription.Status.CANCELLED, subscription.Status.EXPIRED,
                               subscription.Status.FROZEN}:
        return subscription
    boundary = subscription.ends_at
    if boundary is None or boundary > (at or timezone.now()):
        return subscription
    for ent in subscription.internet_entitlements.select_for_update().exclude(
            status__in=(InternetEntitlement.Status.EXPIRED, InternetEntitlement.Status.CANCELLED)):
        expire_internet_entitlement(ent, reason='membership_expired', effective_at=boundary)
    subscription.status = subscription.Status.EXPIRED
    subscription.save(update_fields=('status', 'updated_at'))
    _audit(None, 'membership.expired', subscription_id=subscription.pk,
           boundary=boundary.isoformat())
    return subscription


@transaction.atomic
def apply_payment_reversal(payment, *, actor=None):
    """Apply Internet economics/access after the existing full reversal succeeds."""
    from core.models import Payment
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if not payment.is_reversed:
        raise ValidationError('Payment has not been reversed.')
    shares = InternetRevenueShare.objects.select_for_update().filter(
        Q(payment=payment) | Q(subscription__payment=payment))
    # Membership shares were snapshotted from the subscription allocation and legacy
    # rows may not carry a payment FK; provenance through subscription is authoritative.
    for share in shares.distinct():
        adjustment = record_payment_reversal_adjustment(share, payment=payment)
        cancel_internet_entitlement(share.entitlement, actor=actor,
                                    reason='payment_reversed', effective_at=payment.reversed_at)
        _audit(actor, 'internet.partner_adjustment_created', adjustment_id=adjustment.pk,
               revenue_share_id=share.pk, payment_id=payment.pk)
    for subscription in payment.membership_subscriptions.select_for_update().all():
        cancel_membership(subscription, actor=actor, reason='payment_reversed',
                          at=payment.reversed_at)
    _audit(actor, 'internet.payment_reversal_applied', payment_id=payment.pk)
