"""Staff notification helpers.

Future WhatsApp/Telegram notes: WhatsApp must not be a core staff channel. The
Official WhatsApp Business Platform may be unavailable for Syria-based businesses
or numbers. Any WhatsApp integration should stay optional and disabled by
default; Telegram can be added later as a simpler optional fallback.
Notification sounds are generated in-browser using the Web Audio API; no audio asset is required.
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import (
    NotificationEvent,
    NotificationLog,
    NotificationPreference,
    NotificationRecipient,
    Order,
    OrderItem,
)

logger = logging.getLogger(__name__)

PREP_EVENTS = {'new_prep_item', 'prep_item_ready', 'prep_item_cancelled'}
PAYMENT_EVENTS = {'payment_pending', 'partial_payment_requested', 'discount_added'}
MANAGER_EVENTS = {'manager_approval_needed'}
DAILY_EVENTS = {'close_day_finalized'}
DELIVERY_EVENTS = {
    'delivery_order_created',
    'delivery_order_confirmed',
    'delivery_ready_for_delivery',
    'delivery_out_for_delivery',
    'delivery_delivered',
    'delivery_cancelled',
}

# Notification visibility uses the same effective capability resolver as page
# access. Role/station still determines who is a candidate recipient; capability
# determines whether that candidate is allowed to see/act on the event.
NOTIFICATION_EVENT_CAPABILITIES = {
    'new_order': 'orders',
    'order_edited': 'orders',
    'order_cancelled': 'orders',
    'new_prep_item': 'kitchen_board',
    'prep_item_ready': 'kitchen_board',
    'prep_item_cancelled': 'kitchen_board',
    'payment_pending': 'cashier',
    'partial_payment_requested': 'partial_payment_approval',
    'discount_added': 'cashier',
    'manager_approval_needed': 'partial_payment_approval',
    'close_day_finalized': 'finance',
    **{event_type: 'delivery_management' for event_type in DELIVERY_EVENTS},
}

ACCOUNT_ROLE_TO_NOTIFICATION_ROLES = {
    'admin': ('admin',),
    'cashier': ('cashier',),
    'waiter': ('waiter', 'service'),
    'kitchen': ('kitchen',),
}


def notification_capability_for_event_type(event_type):
    return NOTIFICATION_EVENT_CAPABILITIES.get(event_type, 'staff_home')


def role_for_station(station):
    code = getattr(station, 'code', '') or getattr(station, 'station_type', '') or ''
    if code == 'kitchen':
        return 'kitchen'
    if code in {'bar', 'cashier', 'internet', 'service'}:
        return 'cashier'
    return ''


def _roles_for_event(event_type, station=None, target_role=''):
    if target_role:
        return [target_role]
    if event_type in {'new_order', 'order_edited', 'order_cancelled', 'delivery_order_created'}:
        return ['admin', 'cashier', 'service']
    if event_type in {'delivery_order_confirmed', 'delivery_ready_for_delivery', 'delivery_out_for_delivery'}:
        return ['admin', 'cashier', 'service']
    if event_type in {'delivery_delivered', 'delivery_cancelled'}:
        return ['admin', 'cashier']
    if event_type in PREP_EVENTS:
        role = role_for_station(station)
        return [role] if role else ['kitchen', 'cashier']
    if event_type in PAYMENT_EVENTS:
        return ['admin', 'cashier']
    if event_type in MANAGER_EVENTS:
        return ['admin']
    if event_type in DAILY_EVENTS:
        return ['admin', 'cashier']
    return ['admin']


def link_for_event(event):
    code = getattr(event.target_station, 'code', '') if event.target_station_id else ''
    if event.event_type in PREP_EVENTS:
        return reverse('staff_prep_station', kwargs={'station_code': code}) if code else reverse('staff_prep')
    if event.event_type in PAYMENT_EVENTS or event.event_type in MANAGER_EVENTS:
        return reverse('staff_cashier_order', kwargs={'public_code': event.order.public_code}) if event.order_id else reverse('staff_cashier')
    if event.event_type in DAILY_EVENTS:
        return reverse('staff_close_day')
    return reverse('staff_delivery') if str(event.event_type).startswith('delivery_') else reverse('staff_orders')


def _safe_enqueue_push(event_id):
    # Import lazily to keep the notification source independent of the delivery
    # adapter. The callback itself also catches every error so a provider/queue
    # problem cannot turn a committed order action into an HTTP failure.
    try:
        from core.services.notification_delivery import safe_enqueue_push_deliveries
        safe_enqueue_push_deliveries(event_id)
    except Exception:  # pragma: no cover - final isolation boundary
        logger.exception('Failed to schedule push delivery for notification event_id=%s', event_id)


def create_notification(
    event_type,
    title_ar,
    message_ar='',
    order=None,
    order_item=None,
    target_station=None,
    target_role='',
    created_by=None,
):
    try:
        with transaction.atomic():
            event = NotificationEvent.objects.create(
                event_type=event_type,
                title_ar=title_ar,
                message_ar=message_ar,
                order=order,
                order_item=order_item,
                target_station=target_station,
                target_role=target_role or '',
                created_by=created_by if getattr(created_by, 'is_authenticated', False) else None,
            )
            roles = _roles_for_event(event_type, target_station, target_role)
            recipients = [
                NotificationRecipient(
                    notification_event=event,
                    role=role,
                    station=target_station,
                )
                for role in roles
            ]
            # Do not create a second explicit recipient row for every superuser.
            # Admin users already see the complete in-app stream and push routing
            # explicitly includes admin where appropriate. The old duplicate row
            # made preparation events leak into admin push delivery.
            NotificationRecipient.objects.bulk_create(recipients)
            NotificationLog.objects.create(
                notification_event=event,
                channel=NotificationLog.Channel.SYSTEM,
                recipient_role=','.join(roles),
                recipient_station=target_station,
                status=NotificationLog.Status.SENT,
                sent_at=timezone.now(),
            )
            transaction.on_commit(
                lambda event_id=event.pk: _safe_enqueue_push(event_id),
                robust=True,
            )
            return event
    except Exception:
        logger.exception('Failed to create staff notification')
        return None


def notify_order_created(order, created_by=None):
    create_notification(
        'delivery_order_created' if order.is_delivery else 'new_order',
        f'طلب جديد {order.display_number}',
        order.location_label,
        order=order,
        created_by=created_by,
    )
    create_notification(
        'payment_pending',
        f'الدفع بانتظار الكاشير {order.display_number}',
        order.location_label,
        order=order,
        target_role='cashier',
        created_by=created_by,
    )
    for item in order.items.select_related('prep_station'):
        if item.prep_status != OrderItem.PrepStatus.NO_PREP:
            create_notification(
                'new_prep_item',
                'عنصر جديد للتحضير',
                f'{item.product_name_ar_snapshot} × {item.quantity} — {order.display_number}',
                order=order,
                order_item=item,
                target_station=item.prep_station,
                created_by=created_by,
            )


def user_is_notification_admin(user):
    return bool(getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'admin')


def notification_group_key_for_event(event, *, collapse_prep_by_order=True):
    if collapse_prep_by_order and event.event_type == 'new_prep_item' and event.order_id:
        station_id = event.target_station_id or 'none'
        return f'prep-order:{event.order_id}:station:{station_id}'
    if event.id:
        return f'event:{event.id}'
    return 'fallback:{event_type}:{order_id}:{item_id}:{station_id}:{role}'.format(
        event_type=event.event_type or '',
        order_id=event.order_id or '',
        item_id=event.order_item_id or '',
        station_id=event.target_station_id or '',
        role=event.target_role or '',
    )


def _context_label(event):
    if event.target_station_id:
        return event.target_station.name_ar
    if event.target_role == 'cashier' or event.event_type in PAYMENT_EVENTS:
        return 'الكاشير'
    if event.target_role == 'admin' or event.event_type in MANAGER_EVENTS:
        return 'الإدارة'
    return ''


def _card_from_group(group_key, recipients):
    recipients = sorted(recipients, key=lambda r: r.notification_event.created_at, reverse=True)
    representative = recipients[0]
    events = []
    seen_events = set()
    for recipient in recipients:
        event = recipient.notification_event
        event_key = event.id or id(event)
        if event_key not in seen_events:
            seen_events.add(event_key)
            events.append(event)
    event = representative.notification_event
    title = event.title_ar
    message = event.message_ar
    if len(events) > 1 and event.event_type == 'new_prep_item':
        title = f'طلب جديد {event.order.display_number} — عناصر للتحضير' if event.order_id else 'عناصر جديدة للتحضير'
        item_messages = [e.message_ar for e in events if e.message_ar]
        message = '، '.join(item_messages[:3])
        if len(item_messages) > 3:
            message = f'{message}، و{len(item_messages) - 3} عناصر أخرى'
    order_number = event.order.display_number if event.order_id else ''
    recipient_ids = sorted({r.id for r in recipients})
    unread = any(not r.is_read for r in recipients)
    return {
        'id': group_key,
        'group_key': group_key,
        'recipient_ids': recipient_ids,
        'recipient_count': len(recipient_ids),
        'event_count': len(events),
        'title': title,
        'message': message,
        'order_number': order_number,
        'station': _context_label(event),
        'created_at': event.created_at,
        'created_at_display': event.created_at.strftime('%Y-%m-%d %H:%M'),
        'link': link_for_event(event),
        'is_read': not unread,
    }


def group_notification_recipients_for_user(user, *, unread_only=False, limit=50, fetch_limit=250):
    qs = visible_recipients_for(user).select_related(
        'notification_event',
        'notification_event__order',
        'notification_event__order_item',
        'notification_event__target_station',
        'station',
    ).filter(notification_event__is_active=True)
    if unread_only:
        qs = qs.filter(is_read=False)
    rows = list(qs.order_by('-notification_event__created_at', '-created_at')[:fetch_limit])
    grouped = {}
    order = []
    collapse_prep = user_is_notification_admin(user)
    for recipient in rows:
        event = recipient.notification_event
        key = notification_group_key_for_event(event, collapse_prep_by_order=collapse_prep)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(recipient)
    cards = [_card_from_group(key, grouped[key]) for key in order]
    cards.sort(key=lambda c: c['created_at'], reverse=True)
    return cards[:limit]


def get_user_notification_events(user):
    return group_notification_recipients_for_user(user)


def get_unread_grouped_notification_count(user):
    return len(group_notification_recipients_for_user(user, unread_only=True, limit=500, fetch_limit=500))


def mark_grouped_notification_read(user, group_key=None, recipient_id=None):
    qs = visible_recipients_for(user).filter(is_read=False, notification_event__is_active=True)
    if recipient_id:
        try:
            recipient = qs.select_related('notification_event').get(pk=recipient_id)
        except NotificationRecipient.DoesNotExist:
            return 0
        group_key = notification_group_key_for_event(
            recipient.notification_event,
            collapse_prep_by_order=user_is_notification_admin(user),
        )
    if group_key:
        cards = group_notification_recipients_for_user(
            user,
            unread_only=True,
            limit=500,
            fetch_limit=500,
        )
        recipient_ids = []
        for card in cards:
            if card['group_key'] == group_key:
                recipient_ids = card['recipient_ids']
                break
        qs = qs.filter(pk__in=recipient_ids)
    return qs.update(is_read=True, read_at=timezone.now(), delivered_at=timezone.now())


def visible_recipients_for(user):
    role = getattr(user, 'role', '')
    if not getattr(user, 'is_active', False):
        return NotificationRecipient.objects.none()

    if user_is_notification_admin(user):
        candidates = NotificationRecipient.objects.all()
    else:
        notification_roles = ACCOUNT_ROLE_TO_NOTIFICATION_ROLES.get(role, (role,))
        candidates = NotificationRecipient.objects.filter(
            Q(user=user) | Q(role__in=notification_roles)
        )

    allowed_event_types = [
        event_type
        for event_type, _label in NotificationEvent.EventType.choices
        if user_has_capability(user, notification_capability_for_event_type(event_type))
    ]
    return candidates.filter(notification_event__event_type__in=allowed_event_types)
