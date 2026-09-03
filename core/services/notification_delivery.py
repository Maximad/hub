"""Durable staff push routing and delivery.

NotificationEvent / NotificationRecipient remain the source of truth. This
module only adds a browser-push delivery channel backed by NotificationLog.
Network sends never run inside order/request transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    NotificationEvent,
    NotificationLog,
    NotificationPreference,
    PushSubscription,
)
from core.notifications import link_for_event, role_for_station
from core.services.push import PushPayload, PushTransportError, get_push_transport

logger = logging.getLogger(__name__)

PUSH_MAX_ATTEMPTS = 5
PUSH_CLAIM_LEASE_SECONDS = 90
PUSH_RETRY_DELAYS_SECONDS = (30, 120, 300, 900)


@dataclass(frozen=True)
class PushRoute:
    roles: tuple[str, ...]
    preference_field: str
    aggregate_prep: bool = False


# This is intentionally smaller than the in-app notification matrix. Push is
# reserved for events that need timely attention on a backgrounded device.
PUSH_ROUTES = {
    NotificationEvent.EventType.NEW_ORDER: PushRoute(
        ('admin', 'cashier', 'service'), 'notify_new_orders'
    ),
    NotificationEvent.EventType.DELIVERY_ORDER_CREATED: PushRoute(
        ('admin', 'cashier', 'service'), 'notify_new_orders'
    ),
    NotificationEvent.EventType.NEW_PREP_ITEM: PushRoute(
        ('admin',), 'notify_prep_items', aggregate_prep=True
    ),
    NotificationEvent.EventType.PREP_ITEM_READY: PushRoute(
        ('admin', 'service'), 'notify_prep_items'
    ),
    NotificationEvent.EventType.MANAGER_APPROVAL_NEEDED: PushRoute(
        ('admin',), 'notify_manager_approvals'
    ),
}

# Notification roles predate the current account role labels. Keep the mapping
# explicit so a "service" audience resolves to waiters without inventing a new
# account role.
ROLE_TO_ACCOUNT_ROLES = {
    'admin': ('admin',),
    'cashier': ('cashier',),
    'service': ('waiter',),
    'waiter': ('waiter',),
    'kitchen': ('kitchen',),
}


def push_route_for_event(event: NotificationEvent) -> PushRoute | None:
    route = PUSH_ROUTES.get(event.event_type)
    if route is None:
        return None
    if event.event_type == NotificationEvent.EventType.NEW_PREP_ITEM:
        station_role = role_for_station(event.target_station) if event.target_station_id else ''
        roles = list(route.roles)
        if station_role:
            roles.append(station_role)
        elif 'kitchen' not in roles:
            roles.append('kitchen')
        return PushRoute(tuple(dict.fromkeys(roles)), route.preference_field, True)
    return route


def push_dedupe_key(event: NotificationEvent) -> str:
    route = push_route_for_event(event)
    if route and route.aggregate_prep and event.order_id:
        return f'prep-order:{event.order_id}:station:{event.target_station_id or "none"}'
    return f'event:{event.pk}'


def _role_matches_user(role: str, user) -> bool:
    if role == 'admin' and (getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'admin'):
        return True
    return getattr(user, 'role', '') in ROLE_TO_ACCOUNT_ROLES.get(role, (role,))


def user_is_push_target(event: NotificationEvent, user) -> bool:
    if not getattr(user, 'is_active', False):
        return False
    route = push_route_for_event(event)
    if route is None:
        return False
    if event.recipients.filter(user_id=user.pk).exists():
        return True
    return any(_role_matches_user(role, user) for role in route.roles)


def _target_users(event: NotificationEvent):
    route = push_route_for_event(event)
    User = get_user_model()
    if route is None:
        return User.objects.none()

    query = Q(pk__in=event.recipients.filter(user__isnull=False).values('user_id'))
    for role in route.roles:
        if role == 'admin':
            query |= Q(is_superuser=True) | Q(role='admin')
        else:
            query |= Q(role__in=ROLE_TO_ACCOUNT_ROLES.get(role, (role,)))
    return User.objects.filter(is_active=True).filter(query).distinct()


def _preference_allows(event: NotificationEvent, user) -> bool:
    route = push_route_for_event(event)
    if route is None:
        return False
    try:
        preference = user.notification_preference
    except NotificationPreference.DoesNotExist:
        return False
    if not preference.enable_browser_notifications:
        return False
    return bool(getattr(preference, route.preference_field, False))


def _event_is_deliverable(event: NotificationEvent, *, now=None) -> bool:
    now = now or timezone.now()
    if not event.is_active:
        return False
    if event.expires_at and event.expires_at <= now:
        return False
    return push_route_for_event(event) is not None


def enqueue_push_deliveries_for_event(event_or_id) -> int:
    """Create idempotent pending delivery logs for one notification event.

    This performs database work only. It is safe to call from transaction
    on-commit callbacks because provider/network code is never invoked here.
    """

    if not getattr(settings, 'PUSH_NOTIFICATIONS_ENABLED', False):
        return 0

    if isinstance(event_or_id, NotificationEvent):
        event = event_or_id
    else:
        event = (
            NotificationEvent.objects.select_related('target_station', 'order')
            .filter(pk=event_or_id)
            .first()
        )
    if event is None or not _event_is_deliverable(event):
        return 0

    user_ids = [user.pk for user in _target_users(event) if _preference_allows(event, user)]
    if not user_ids:
        return 0

    subscriptions = PushSubscription.objects.filter(
        user_id__in=user_ids,
        provider=PushSubscription.Provider.WEBPUSH,
        permission_state=PushSubscription.PermissionState.GRANTED,
        is_active=True,
        revoked_at__isnull=True,
    ).select_related('user')

    now = timezone.now()
    dedupe_key = push_dedupe_key(event)
    created_count = 0
    for subscription in subscriptions:
        try:
            _, created = NotificationLog.objects.get_or_create(
                push_subscription=subscription,
                channel=NotificationLog.Channel.BROWSER,
                dedupe_key=dedupe_key,
                defaults={
                    'notification_event': event,
                    'recipient_user': subscription.user,
                    'recipient_role': getattr(subscription.user, 'role', '') or '',
                    'recipient_station': event.target_station,
                    'status': NotificationLog.Status.PENDING,
                    'next_attempt_at': now,
                },
            )
        except IntegrityError:
            # A concurrent enqueue won the unique dedupe constraint.
            created = False
        created_count += int(created)
    return created_count


def safe_enqueue_push_deliveries(event_id: int) -> None:
    """Best-effort on-commit wrapper that can never fail the originating action."""

    try:
        enqueue_push_deliveries_for_event(event_id)
    except Exception:  # pragma: no cover - defensive request isolation
        logger.exception('Failed to enqueue push delivery for notification event_id=%s', event_id)


def _aggregate_prep_count(event: NotificationEvent) -> int:
    if not event.order_id:
        return 1
    return NotificationEvent.objects.filter(
        event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
        order_id=event.order_id,
        target_station_id=event.target_station_id,
        is_active=True,
    ).count()


def build_push_payload(event: NotificationEvent) -> PushPayload:
    """Build a lock-screen-safe payload without customer/private details."""

    order_number = event.order.display_number if event.order_id else ''
    suffix = f' {order_number}' if order_number else ''
    dedupe_key = push_dedupe_key(event)

    if event.event_type == NotificationEvent.EventType.NEW_ORDER:
        title = f'طلب جديد{suffix}'
        body = 'يوجد طلب جديد بانتظار المتابعة.'
    elif event.event_type == NotificationEvent.EventType.DELIVERY_ORDER_CREATED:
        title = f'طلب توصيل جديد{suffix}'
        body = 'يوجد طلب توصيل جديد بانتظار المتابعة.'
    elif event.event_type == NotificationEvent.EventType.NEW_PREP_ITEM:
        count = _aggregate_prep_count(event)
        title = f'عناصر جديدة للتحضير{suffix}'
        body = f'{count} عنصر بانتظار التحضير.' if count == 1 else f'{count} عناصر بانتظار التحضير.'
    elif event.event_type == NotificationEvent.EventType.PREP_ITEM_READY:
        title = f'عنصر جاهز{suffix}'
        body = 'يوجد عنصر جاهز للاستلام.'
    elif event.event_type == NotificationEvent.EventType.MANAGER_APPROVAL_NEEDED:
        title = f'مطلوب موافقة مدير{suffix}'
        body = 'يوجد إجراء بانتظار موافقة الإدارة.'
    else:  # guarded by routing, kept defensive for future route additions
        title = 'تنبيه جديد من هَبّ'
        body = 'يوجد تحديث جديد بانتظار المتابعة.'

    return PushPayload(
        title=title,
        body=body,
        link=link_for_event(event),
        tag=dedupe_key,
    )


def _claim_next_delivery(*, now=None):
    now = now or timezone.now()
    lease_until = now + timedelta(seconds=PUSH_CLAIM_LEASE_SECONDS)
    with transaction.atomic():
        delivery = (
            NotificationLog.objects.select_for_update(skip_locked=True)
            .filter(
                channel=NotificationLog.Channel.BROWSER,
                status=NotificationLog.Status.PENDING,
                push_subscription__isnull=False,
            )
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .order_by('next_attempt_at', 'created_at', 'pk')
            .first()
        )
        if delivery is None:
            return None
        delivery.attempt_count += 1
        delivery.next_attempt_at = lease_until
        delivery.save(update_fields=('attempt_count', 'next_attempt_at', 'updated_at'))
        return delivery.pk


def _mark_skipped(delivery: NotificationLog, error_code: str) -> str:
    delivery.status = NotificationLog.Status.SKIPPED
    delivery.error_code = error_code
    delivery.error_message = ''
    delivery.next_attempt_at = None
    delivery.save(update_fields=(
        'status', 'error_code', 'error_message', 'next_attempt_at', 'updated_at'
    ))
    return 'skipped'


def _retry_delay(attempt_count: int) -> int:
    index = max(0, min(attempt_count - 1, len(PUSH_RETRY_DELAYS_SECONDS) - 1))
    return PUSH_RETRY_DELAYS_SECONDS[index]


def _mark_failure(delivery: NotificationLog, subscription: PushSubscription, exc: PushTransportError, *, now) -> str:
    subscription.failure_count += 1
    subscription.save(update_fields=('failure_count', 'updated_at'))

    delivery.provider = PushSubscription.Provider.WEBPUSH
    delivery.error_code = exc.error_code[:80]
    delivery.error_message = ''

    if exc.permanent:
        subscription.is_active = False
        subscription.revoked_at = now
        subscription.save(update_fields=('is_active', 'revoked_at', 'updated_at'))
        delivery.status = NotificationLog.Status.FAILED
        delivery.next_attempt_at = None
        delivery.save(update_fields=(
            'provider', 'error_code', 'error_message', 'status', 'next_attempt_at', 'updated_at'
        ))
        NotificationLog.objects.filter(
            push_subscription=subscription,
            channel=NotificationLog.Channel.BROWSER,
            status=NotificationLog.Status.PENDING,
        ).exclude(pk=delivery.pk).update(
            status=NotificationLog.Status.SKIPPED,
            error_code='subscription_inactive',
            error_message='',
            next_attempt_at=None,
            updated_at=now,
        )
        return 'failed'

    if delivery.attempt_count >= PUSH_MAX_ATTEMPTS:
        delivery.status = NotificationLog.Status.FAILED
        delivery.next_attempt_at = None
        outcome = 'failed'
    else:
        delivery.status = NotificationLog.Status.PENDING
        delivery.next_attempt_at = now + timedelta(seconds=_retry_delay(delivery.attempt_count))
        outcome = 'retried'
    delivery.save(update_fields=(
        'provider', 'error_code', 'error_message', 'status', 'next_attempt_at', 'updated_at'
    ))
    return outcome


def _deliver_claimed(delivery_id: int, transport, *, now=None) -> str:
    now = now or timezone.now()
    delivery = (
        NotificationLog.objects.select_related(
            'notification_event',
            'notification_event__order',
            'notification_event__target_station',
            'push_subscription',
            'push_subscription__user',
            'recipient_user',
        )
        .filter(pk=delivery_id)
        .first()
    )
    if delivery is None or delivery.status != NotificationLog.Status.PENDING:
        return 'skipped'

    event = delivery.notification_event
    subscription = delivery.push_subscription
    user = subscription.user if subscription else None

    if subscription is None or not subscription.is_active or subscription.revoked_at:
        return _mark_skipped(delivery, 'subscription_inactive')
    if subscription.permission_state != PushSubscription.PermissionState.GRANTED:
        return _mark_skipped(delivery, 'permission_not_granted')
    if user is None or not user.is_active:
        return _mark_skipped(delivery, 'recipient_inactive')
    if not _event_is_deliverable(event, now=now):
        return _mark_skipped(delivery, 'event_inactive')
    if not user_is_push_target(event, user):
        return _mark_skipped(delivery, 'recipient_not_targeted')
    if not _preference_allows(event, user):
        return _mark_skipped(delivery, 'preference_disabled')

    try:
        payload = build_push_payload(event)
        result = transport.send(subscription, payload)
        if not result.accepted:
            status_code = result.status_code
            raise PushTransportError(
                'subscription_gone' if status_code in {404, 410} else (
                    f'provider_http_{status_code}' if status_code else 'provider_error'
                ),
                permanent=status_code in {404, 410},
                status_code=status_code,
            )
    except PushTransportError as exc:
        return _mark_failure(delivery, subscription, exc, now=now)
    except Exception:
        # Never persist or log provider exception text: some libraries include
        # endpoint/key material in exception strings.
        exc = PushTransportError('provider_error', permanent=False)
        return _mark_failure(delivery, subscription, exc, now=now)

    delivery.status = NotificationLog.Status.SENT
    delivery.sent_at = now
    delivery.provider = getattr(transport, 'provider', '')[:20]
    delivery.provider_message_id = (result.provider_message_id or '')[:255]
    delivery.error_code = ''
    delivery.error_message = ''
    delivery.next_attempt_at = None
    delivery.save(update_fields=(
        'status', 'sent_at', 'provider', 'provider_message_id', 'error_code',
        'error_message', 'next_attempt_at', 'updated_at'
    ))
    if subscription.failure_count:
        subscription.failure_count = 0
        subscription.save(update_fields=('failure_count', 'updated_at'))
    return 'sent'


def run_notification_worker_cycle(*, limit=50, transport=None):
    summary = {
        'enabled': bool(getattr(settings, 'PUSH_NOTIFICATIONS_ENABLED', False)),
        'claimed': 0,
        'sent': 0,
        'retried': 0,
        'failed': 0,
        'skipped': 0,
    }
    errors = []
    if not summary['enabled']:
        return summary, errors

    if transport is None:
        try:
            transport = get_push_transport()
        except Exception:
            errors.append(('transport', 'configuration_error'))
            return summary, errors

    for _ in range(limit):
        delivery_id = _claim_next_delivery(now=timezone.now())
        if delivery_id is None:
            break
        summary['claimed'] += 1
        outcome = _deliver_claimed(delivery_id, transport, now=timezone.now())
        if outcome in summary:
            summary[outcome] += 1
        else:
            summary['skipped'] += 1
    return summary, errors
