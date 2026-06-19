"""Staff notification helpers.

Future WhatsApp/Telegram notes: WhatsApp must not be a core staff channel. The
Official WhatsApp Business Platform may be unavailable for Syria-based businesses
or numbers. Any WhatsApp integration should stay optional and disabled by
default; Telegram can be added later as a simpler optional fallback.
Notification sounds are generated in-browser using the Web Audio API; no audio asset is required.
"""
import logging
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from core.models import NotificationEvent, NotificationLog, NotificationPreference, NotificationRecipient, Order, OrderItem

logger = logging.getLogger(__name__)

PREP_EVENTS = {'new_prep_item','prep_item_ready','prep_item_cancelled'}
PAYMENT_EVENTS = {'payment_pending','partial_payment_requested','discount_added'}
MANAGER_EVENTS = {'manager_approval_needed'}
DAILY_EVENTS = {'close_day_finalized'}


def role_for_station(station):
    code = getattr(station, 'code', '') or getattr(station, 'station_type', '') or ''
    if code == 'kitchen': return 'kitchen'
    if code in {'bar','cashier','internet','service'}: return 'cashier'
    return ''


def _roles_for_event(event_type, station=None, target_role=''):
    if target_role: return [target_role]
    if event_type in {'new_order','order_edited','order_cancelled','delivery_order_created'}: return ['admin','cashier','waiter']
    if event_type in PREP_EVENTS:
        role = role_for_station(station)
        return ['admin', role] if role else ['admin','kitchen','cashier']
    if event_type in PAYMENT_EVENTS: return ['admin','cashier']
    if event_type in MANAGER_EVENTS: return ['admin']
    if event_type in DAILY_EVENTS: return ['admin','cashier']
    return ['admin']


def link_for_event(event):
    code = getattr(event.target_station, 'code', '') if event.target_station_id else ''
    if event.event_type in PREP_EVENTS:
        return reverse('staff_prep_station', kwargs={'station_code': code}) if code else reverse('staff_prep')
    if event.event_type in PAYMENT_EVENTS or event.event_type in MANAGER_EVENTS:
        return reverse('staff_cashier_order', kwargs={'public_code': event.order.public_code}) if event.order_id else reverse('staff_cashier')
    if event.event_type in DAILY_EVENTS: return reverse('staff_close_day')
    return reverse('staff_orders')


def create_notification(event_type, title_ar, message_ar='', order=None, order_item=None, target_station=None, target_role='', created_by=None):
    try:
        with transaction.atomic():
            event = NotificationEvent.objects.create(event_type=event_type, title_ar=title_ar, message_ar=message_ar, order=order, order_item=order_item, target_station=target_station, target_role=target_role or '', created_by=created_by if getattr(created_by,'is_authenticated',False) else None)
            User = get_user_model()
            roles = _roles_for_event(event_type, target_station, target_role)
            recipients = []
            for role in roles:
                recipients.append(NotificationRecipient(notification_event=event, role=role, station=target_station))
            for user in User.objects.filter(is_active=True).filter(is_superuser=True):
                recipients.append(NotificationRecipient(notification_event=event, user=user, role='admin', station=target_station))
            NotificationRecipient.objects.bulk_create(recipients)
            NotificationLog.objects.create(notification_event=event, channel=NotificationLog.Channel.SYSTEM, recipient_role=','.join(roles), recipient_station=target_station, status=NotificationLog.Status.SENT, sent_at=timezone.now())
            return event
    except Exception:
        logger.exception('Failed to create staff notification')
        return None


def notify_order_created(order, created_by=None):
    create_notification('delivery_order_created' if order.is_delivery else 'new_order', f'طلب جديد {order.display_number}', order.location_label, order=order, created_by=created_by)
    create_notification('payment_pending', f'الدفع بانتظار الكاشير {order.display_number}', order.location_label, order=order, target_role='cashier', created_by=created_by)
    for item in order.items.select_related('prep_station'):
        if item.prep_status != OrderItem.PrepStatus.NO_PREP:
            create_notification('new_prep_item', 'عنصر جديد للتحضير', f'{item.product_name_ar_snapshot} × {item.quantity} — {order.display_number}', order=order, order_item=item, target_station=item.prep_station, created_by=created_by)


def visible_recipients_for(user):
    role = getattr(user, 'role', '')
    if user.is_superuser or role == 'admin':
        return NotificationRecipient.objects.all()
    return NotificationRecipient.objects.filter(user=user) | NotificationRecipient.objects.filter(role=role)
