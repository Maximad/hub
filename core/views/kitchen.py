from collections import OrderedDict

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import (
    can_access_cashier,
    can_access_pos,
    is_kitchen,
    is_owner_or_admin,
    require_staff_capability,
)
from catalog.models import PrepStation
from core.models import ActivityLog, Order, OrderItem
from core.settings_helpers import get_page_setting

ACTIVE_PREP_STATUSES = [
    OrderItem.PrepStatus.PENDING,
    OrderItem.PrepStatus.ACCEPTED,
    OrderItem.PrepStatus.PREPARING,
]
VISIBLE_PREP_STATUSES = ACTIVE_PREP_STATUSES + [OrderItem.PrepStatus.READY]
STATUS_FILTERS = [
    ('active', 'الكل'),
    (OrderItem.PrepStatus.PENDING, 'جديد'),
    (OrderItem.PrepStatus.PREPARING, 'قيد التحضير'),
    (OrderItem.PrepStatus.READY, 'جاهز'),
]
FULFILLMENT_FILTERS = [
    (Order.FulfillmentMode.INSIDE_SPACE, 'داخل المكان'),
    (Order.FulfillmentMode.TABLE, 'طاولة'),
    (Order.FulfillmentMode.DELIVERY, 'توصيل'),
    (Order.FulfillmentMode.TAKEAWAY, 'تيك أواي'),
]
NEXT_STATUS_BY_ACTION = {
    'accept': OrderItem.PrepStatus.ACCEPTED,
    'prepare': OrderItem.PrepStatus.PREPARING,
    'ready': OrderItem.PrepStatus.READY,
    'served': OrderItem.PrepStatus.SERVED,
    'cancel': OrderItem.PrepStatus.CANCELLED,
}
ALLOWED_TRANSITIONS = {
    OrderItem.PrepStatus.PENDING: {OrderItem.PrepStatus.ACCEPTED, OrderItem.PrepStatus.CANCELLED},
    OrderItem.PrepStatus.ACCEPTED: {OrderItem.PrepStatus.PREPARING, OrderItem.PrepStatus.CANCELLED},
    OrderItem.PrepStatus.PREPARING: {OrderItem.PrepStatus.READY, OrderItem.PrepStatus.CANCELLED},
    OrderItem.PrepStatus.READY: {OrderItem.PrepStatus.SERVED, OrderItem.PrepStatus.CANCELLED},
}
ACTION_LABELS = {
    'accept': 'استلام',
    'prepare': 'بدء التحضير',
    'ready': 'جاهز',
    'served': 'تم التسليم',
    'cancel': 'إلغاء',
}


def _kitchen_order_notes(order):
    lines = []
    for line in (order.notes or '').splitlines():
        clean = line.strip()
        if not clean or clean.lower().startswith('source:'):
            continue
        lines.append(clean)
    return '\n'.join(lines)

def _prep_station_label(station):
    if station:
        return station.name_ar or station.name_en or 'محطة تحضير'
    return 'غير محدد / عام'


def _item_actions(item, user):
    actions = []
    owner_or_admin = is_owner_or_admin(user)
    can_serve = owner_or_admin or can_access_cashier(user) or can_access_pos(user)
    can_cancel = owner_or_admin or can_access_cashier(user)

    if item.prep_status == OrderItem.PrepStatus.PENDING:
        actions.append(('accept', ACTION_LABELS['accept'], 'hub-button'))
    elif item.prep_status == OrderItem.PrepStatus.ACCEPTED:
        actions.append(('prepare', ACTION_LABELS['prepare'], 'hub-button'))
    elif item.prep_status == OrderItem.PrepStatus.PREPARING:
        actions.append(('ready', ACTION_LABELS['ready'], 'hub-button hub-button-success'))
    elif item.prep_status == OrderItem.PrepStatus.READY and can_serve:
        actions.append(('served', ACTION_LABELS['served'], 'hub-button hub-button-secondary'))

    if can_cancel and item.prep_status not in {OrderItem.PrepStatus.SERVED, OrderItem.PrepStatus.CANCELLED}:
        actions.append(('cancel', ACTION_LABELS['cancel'], 'hub-button hub-button-danger'))
    return actions


def _can_change_to(user, old_status, new_status):
    if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        return False
    if new_status == OrderItem.PrepStatus.SERVED:
        return is_owner_or_admin(user) or can_access_cashier(user) or can_access_pos(user)
    if new_status == OrderItem.PrepStatus.CANCELLED:
        return is_owner_or_admin(user) or can_access_cashier(user)
    if new_status in {OrderItem.PrepStatus.ACCEPTED, OrderItem.PrepStatus.PREPARING, OrderItem.PrepStatus.READY}:
        return is_owner_or_admin(user) or is_kitchen(user) or can_access_cashier(user) or can_access_pos(user)
    return False


def _filtered_items(request):
    status_filter = request.GET.get('status', 'active')
    station_filter = request.GET.get('station', '').strip()
    fulfillment_filter = request.GET.get('fulfillment', '').strip()

    items = (
        OrderItem.objects.select_related('order', 'order__table', 'order__table__room', 'product', 'product__prep_station_ref')
        .exclude(order__status=Order.Status.CANCELLED)
        .order_by('product__prep_station_ref__sort_order', 'product__prep_station_ref__name_ar', 'order__created_at', 'id')
    )

    if status_filter == 'active':
        items = items.filter(prep_status__in=ACTIVE_PREP_STATUSES)
    elif status_filter == 'all_visible':
        items = items.filter(prep_status__in=VISIBLE_PREP_STATUSES)
    elif status_filter in {choice[0] for choice in OrderItem.PrepStatus.choices}:
        items = items.filter(prep_status=status_filter)
    else:
        status_filter = 'active'
        items = items.filter(prep_status__in=ACTIVE_PREP_STATUSES)

    if station_filter == 'none':
        items = items.filter(product__prep_station_ref__isnull=True)
    elif station_filter.isdigit():
        items = items.filter(product__prep_station_ref_id=int(station_filter))
    elif station_filter:
        station_filter = ''

    valid_fulfillment = {choice[0] for choice in Order.FulfillmentMode.choices}
    if fulfillment_filter in valid_fulfillment:
        if fulfillment_filter == Order.FulfillmentMode.TABLE:
            items = items.filter(Q(order__fulfillment_mode=fulfillment_filter) | Q(order__table__isnull=False))
        else:
            items = items.filter(order__fulfillment_mode=fulfillment_filter)
    else:
        fulfillment_filter = ''

    return items, status_filter, station_filter, fulfillment_filter


def _board_context(request):
    items, status_filter, station_filter, fulfillment_filter = _filtered_items(request)
    stations = list(PrepStation.objects.filter(is_active=True).order_by('sort_order', 'name_ar'))
    grouped_map = OrderedDict()
    total_pending = 0

    for item in items:
        station = item.product.prep_station_ref
        key = station.id if station else 'none'
        if key not in grouped_map:
            grouped_map[key] = {'key': key, 'station': station, 'label': _prep_station_label(station), 'items': []}
        if item.prep_status == OrderItem.PrepStatus.PENDING:
            total_pending += 1
        grouped_map[key]['items'].append({'item': item, 'actions': _item_actions(item, request.user), 'order_notes': _kitchen_order_notes(item.order)})

    return {
        'groups': grouped_map.values(),
        'stations': stations,
        'station_choices': [('none', 'غير محدد / عام')] + [(str(station.id), _prep_station_label(station)) for station in stations],
        'status_filters': STATUS_FILTERS,
        'fulfillment_filters': FULFILLMENT_FILTERS,
        'status_filter': status_filter,
        'station_filter': station_filter,
        'fulfillment_filter': fulfillment_filter,
        'pending_count': total_pending,
        'page_setting': get_page_setting('staff_kitchen', 'المطبخ / التحضير', 'Kitchen'),
    }


@require_staff_capability('kitchen_board')
def staff_kitchen(request):
    template = 'staff/kitchen_partial.html' if request.headers.get('HX-Request') == 'true' else 'staff/kitchen.html'
    return render(request, template, _board_context(request))


@require_staff_capability('kitchen_board')
def staff_kitchen_partial(request):
    return render(request, 'staff/kitchen_partial.html', _board_context(request))


@require_staff_capability('kitchen_board')
def staff_kitchen_order(request, public_code):
    order = get_object_or_404(
        Order.objects.select_related('table', 'table__room').prefetch_related(
            Prefetch('items', queryset=OrderItem.objects.select_related('product', 'product__prep_station_ref'))
        ),
        public_code=public_code,
    )
    return render(request, 'staff/kitchen_order.html', {'order': order})


@require_staff_capability('kitchen_board')
def staff_kitchen_item_status(request, item_id):
    if request.method != 'POST':
        raise Http404()
    action = request.POST.get('action', '').strip()
    new_status = NEXT_STATUS_BY_ACTION.get(action)
    if not new_status:
        messages.error(request, 'الإجراء المطلوب غير صالح.')
        return redirect('staff_kitchen')

    item = get_object_or_404(OrderItem.objects.select_related('order', 'product'), pk=item_id)
    order = item.order
    if order.status == Order.Status.CANCELLED and not is_owner_or_admin(request.user):
        messages.error(request, 'لا يمكن تحديث عنصر من طلب ملغي.')
        return redirect('staff_kitchen')

    old_status = item.prep_status
    if not _can_change_to(request.user, old_status, new_status):
        messages.error(request, 'لا يمكن تنفيذ هذا الانتقال في حالة العنصر الحالية أو بصلاحياتك الحالية.')
        return redirect('staff_kitchen')

    if old_status != new_status:
        with transaction.atomic():
            item.prep_status = new_status
            item.save(update_fields=['prep_status', 'updated_at'])
            ActivityLog.objects.create(
                actor=request.user,
                action='order_item_prep_status_changed',
                details={
                    'actor_username': getattr(request.user, 'username', ''),
                    'order_display_number': order.display_number,
                    'order_public_code': str(order.public_code),
                    'old_status': old_status,
                    'new_status': new_status,
                    'item_name': item.product_name_ar_snapshot,
                    'item_id': item.id,
                },
            )
        messages.success(request, 'تم تحديث حالة العنصر.')
    return redirect('staff_kitchen')
