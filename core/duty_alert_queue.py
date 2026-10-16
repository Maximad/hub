"""Operational outstanding-alert checks and a manager-only unassigned queue.

This is a read-only projection of Order/OrderItem state, not a second task
ledger. Delivery retries and handover requeue reuse existing NotificationLog
deduplication; completed work is never resurrected.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.duty_notification_routing import (
    DUTY_ROUTED_EVENTS, EVENT_CAPABILITIES, _event_slots,
    duty_routing_enabled, duty_target_ids_for_event,
)
from core.models import NotificationEvent, Order, OrderItem
from operations.duty import current_duty_users
from operations.models import BusinessDay
from operations.services import current_business_date

logger = logging.getLogger(__name__)


def event_has_outstanding_work(event, *, now=None):
    """Conservative source-driven predicate for reassignment/escalation.

    A malformed, expired or source-less alert never causes a new push.
    Reading an alert is unrelated to the source's actual completion state.
    """
    now = now or timezone.now()
    if not event.is_active or (event.expires_at and event.expires_at <= now):
        return False
    if event.event_type == NotificationEvent.EventType.NEW_ORDER:
        return (
            event.order_id is not None
            and not event.order.is_delivery
            and event.order.status == Order.Status.NEW
        )
    if event.event_type == NotificationEvent.EventType.DELIVERY_ORDER_CREATED:
        return (
            event.order_id is not None
            and event.order.is_delivery
            and event.order.delivery_status == Order.DeliveryStatus.NEW
        )
    if event.event_type == NotificationEvent.EventType.NEW_PREP_ITEM:
        return (
            event.order_item_id is not None
            and event.order_item.prep_status in {
                OrderItem.PrepStatus.NEW,
                OrderItem.PrepStatus.SENT,
                OrderItem.PrepStatus.PENDING,
            }
            and event.order_id is not None
            and event.order.status != Order.Status.CANCELLED
        )
    if event.event_type == NotificationEvent.EventType.PREP_ITEM_READY:
        return (
            event.order_item_id is not None
            and event.order_item.prep_status == OrderItem.PrepStatus.READY
            and event.order_id is not None
            and event.order.status != Order.Status.CANCELLED
        )
    return False


def _open_current_day(day):
    return bool(
        day is not None
        and day.business_date == current_business_date()
        and day.status in {
            BusinessDay.Status.OPEN,
            BusinessDay.Status.CLOSING,
            BusinessDay.Status.REOPENED,
        }
    )


def _outstanding_events(day, *, limit=500):
    return (
        NotificationEvent.objects.filter(
            duty_routing_business_date=day.business_date,
            event_type__in=DUTY_ROUTED_EVENTS,
            is_active=True,
        )
        .select_related('order', 'order_item', 'target_station')
        .order_by('-created_at', '-pk')[:limit]
    )


def _active_duty_holder_ids(day, event):
    required = EVENT_CAPABILITIES[event.event_type]
    for slot in _event_slots(event):
        found = {
            user.pk
            for user in current_duty_users(day, slot)
            if user_has_capability(user, 'staff_home')
            and user_has_capability(user, required)
        }
        if found:
            return found
    return set()


def unassigned_duty_alerts_for_manager(day, user, *, limit=30):
    """Project still-actionable uncovered work for authorized managers.

    No new task or notification rows are written. A hidden, resolved or
    unauthorized event is not returned to the caller.
    """
    if (
        not duty_routing_enabled()
        or not _open_current_day(day)
        or not getattr(user, 'is_active', False)
        or not (user.is_superuser or getattr(user, 'role', '') == 'admin')
        or not user_has_capability(user, 'staff_home')
    ):
        return []
    from core.notifications import link_for_event

    queue = []
    for event in _outstanding_events(day):
        required = EVENT_CAPABILITIES[event.event_type]
        if not user_has_capability(user, required):
            continue
        if not event_has_outstanding_work(event):
            continue
        if _active_duty_holder_ids(day, event):
            continue

        queue.append({
            'event_id': event.pk,
            'title': event.title_ar,
            'created_at': event.created_at,
            'responsibility': ' / '.join(_event_slots(event)) or 'تغطية غير محددة',
            'link': link_for_event(event),
        })
        if len(queue) >= limit:
            break
    return queue


def reenqueue_outstanding_after_duty_change(day_id, responsibility):
    """Best-effort idempotent requeue to accepted holders after commit.

    Adds only delivery logs for still-outstanding work and only for the *new*
    audience. The existing push worker rechecks eligibility at send time.
    """
    if not duty_routing_enabled():
        return 0
    day = BusinessDay.objects.filter(pk=day_id).first()
    if not _open_current_day(day):
        return 0
    from core.services.notification_delivery import enqueue_push_deliveries_for_event

    created = 0
    for event in _outstanding_events(day):
        if responsibility not in _event_slots(event):
            continue
        if not event_has_outstanding_work(event):
            continue
        # If still unassigned, the manager fallback receives the queue instead.
        if not _active_duty_holder_ids(day, event):
            continue
        try:
            created += enqueue_push_deliveries_for_event(event)
        except Exception:
            logger.exception(
                'Failed to reconcile active duty notifications for event_id=%s',
                event.pk,
            )
    return created


def schedule_reenqueue_after_duty_change(day_id, responsibility):
    """Never allow a provider, enqueue or query error to break a handover."""
    try:
        return reenqueue_outstanding_after_duty_change(day_id, responsibility)
    except Exception:
        logger.exception(
            'Active duty notification reconciliation failed day_id=%s role=%s',
            day_id, responsibility,
        )
        return 0
