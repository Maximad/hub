"""Reversible Active Duty notification routing (opt-in, no new broker).

A nullable event routing date marks events created while Active Duty routing is
enabled. Historical events and dedicated private/day-code notices are untouched.
When coverage is absent, managers receive the alert; if nobody can safely
receive that escalation, retain legacy routing rather than lose the event.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q

from accounts.permissions import user_has_capability
from operations.duty import current_duty_users
from operations.models import BusinessDay
from operations.services import current_business_date

logger = logging.getLogger(__name__)

DUTY_ROUTED_EVENTS = frozenset({
    'new_order', 'delivery_order_created', 'new_prep_item', 'prep_item_ready',
})
EVENT_CAPABILITIES = {
    'new_order': 'orders',
    'delivery_order_created': 'delivery_management',
    'new_prep_item': 'kitchen_board',
    'prep_item_ready': 'delivery_management',
}


def duty_routing_enabled():
    return bool(getattr(settings, 'DUTY_NOTIFICATION_ROUTING_ENABLED', False))


def routing_day_for_new_event(event_type):
    """Mark only new supported events during an actual open Business Day."""
    if not duty_routing_enabled() or event_type not in DUTY_ROUTED_EVENTS:
        return None
    day = BusinessDay.objects.filter(
        business_date=current_business_date(),
        status__in=(BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED),
    ).first()
    return day.business_date if day is not None else None


def _station_kind(event):
    station = event.target_station if event.target_station_id else None
    if station is None:
        return ''
    code = getattr(station, 'code', '')
    if code in {'bar', 'kitchen'}:
        return code
    station_type = getattr(station, 'station_type', '')
    return station_type if station_type in {'bar', 'kitchen'} else ''


def _manager_ids(capability):
    User = get_user_model()
    return {
        user.pk for user in User.objects.filter(is_active=True).filter(
            Q(is_superuser=True) | Q(role='admin')
        )
        if user_has_capability(user, 'staff_home') and user_has_capability(user, capability)
    }


def _event_slots(event):
    if event.event_type == 'new_prep_item':
        kind = _station_kind(event)
        return (kind,) if kind else ()
    if event.event_type in {'new_order', 'delivery_order_created', 'prep_item_ready'}:
        return ('service', 'cashier')
    return ()


def _duty_target_ids(day, event):
    required = EVENT_CAPABILITIES[event.event_type]
    for slot in _event_slots(event):
        candidates = [
            user.pk for user in current_duty_users(day, slot)
            if user_has_capability(user, 'staff_home')
            and user_has_capability(user, required)
        ]
        if candidates:
            return frozenset(candidates)
    managers = _manager_ids(required)
    if managers:
        return frozenset(managers)
    logger.warning(
        'No valid duty holder or manager; preserving legacy delivery for notification event_id=%s',
        event.pk,
    )
    return None


def duty_target_ids_for_event(event):
    """Return None for legacy routing or a bounded set of eligible recipients.

    A handover changes eligibility at send time and when in-app lists are read.
    The worker rechecks queued deliveries, so stale pending recipients are
    skipped rather than sent after handover.
    """
    if (
        not duty_routing_enabled()
        or event.event_type not in DUTY_ROUTED_EVENTS
        or event.duty_routing_business_date is None
    ):
        return None
    # Previously routed days retain their historical audience in the inbox.
    # Pending deliveries from previous days still follow the worker's normal
    # freshness rules; introducing reminder/escalation expiry is a later phase.
    if event.duty_routing_business_date != current_business_date():
        return None
    day = BusinessDay.objects.filter(
        business_date=event.duty_routing_business_date,
        status__in=(BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED),
    ).first()
    if day is None:
        return None
    return _duty_target_ids(day, event)


def duty_inbox_scope(user):
    """Return (routed_event_ids, visible_event_ids) for current Business Day.

    This supplements role-based legacy broadcasts so an authorized duty holder
    can receive a task even if their account's old role label differs.
    """
    if not duty_routing_enabled():
        return (), ()
    day = BusinessDay.objects.filter(
        business_date=current_business_date(),
        status__in=(BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED),
    ).first()
    if day is None:
        return (), ()
    from core.models import NotificationEvent
    events = list(
        NotificationEvent.objects.filter(
            duty_routing_business_date=day.business_date,
            event_type__in=DUTY_ROUTED_EVENTS,
        ).select_related('target_station')
    )
    routed = []
    visible = []
    # Resolve each station/event category once per poll, not once per event.
    cached = {}
    for event in events:
        key = (event.event_type, _station_kind(event))
        if key not in cached:
            cached[key] = _duty_target_ids(day, event)
        targets = cached[key]
        if targets is None:
            # No authorized escalation exists: preserve legacy visibility.
            continue
        routed.append(event.pk)
        if user.pk in targets:
            visible.append(event.pk)
    return routed, visible
