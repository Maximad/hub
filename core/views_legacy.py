from django.db import transaction
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
import re

from members.models import MembershipBenefitRule, MembershipPlan, MembershipSubscription, MemberCreditLedger
from members.services import evaluate_membership_benefit, get_active_member_context, resolve_member_from_request
from internet.models import WifiNetwork
from catalog.models import MenuSection, PrepStation, ProductMedia, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment, Tag
from core.settings_helpers import get_page_setting, get_system_settings
from core.currency_forms import CurrencyEntryFormService
from core.internet_billing import calculate_billable_minutes, calculate_metered_session_total, calculate_session_duration_minutes, can_override_session_total, finalize_internet_session
from core.services.bulk_edit import BulkEditValidationError
from core.services.menu_tools import ALLOWED_ACTIONS, apply_product_bulk_action, preview_product_bulk_action
from accounts.permissions import (
    ACCESS_DENIED_MESSAGE, can_approve_partial_payment,
    require_staff_capability, user_has_capability,
)
from core.stock_recipes import deduct_order_item_stock
from core.models import ActivityLog, CancellationReason, CashMovement, Category, DailyClose, Expense, InternetEntitlement, InternetPackage, InternetSession, Member, Order, OrderDiscount, OrderItem, Payment, PostingCommand, Product, Room, SystemSetting, TableArea
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect as collect_order_payment
from events.models import Event
from reservations.models import Reservation
from reservations.services import change_reservation_status, create_reservation
from vendors.models import Vendor, VendorParticipation
from core.notifications import create_notification, notify_order_created
from core.services.margins import item_margin, product_margin_from_values
from core.services.internet_access import (create_commercial_sale, daily_minutes_remaining,
    daily_minutes_used, effectively_active_entitlements, get_effective_network_allowance)


DAMASCUS_TZ = ZoneInfo('Asia/Damascus')
PHONE_ALLOWED_PATTERN = re.compile(r'^[\d\s\-\+\(\)]+$')


def _qr_svg_response(data):
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError as exc:
        return HttpResponse('qrcode package is required to render QR SVG.', status=503, content_type='text/plain; charset=utf-8')
    factory = qrcode.image.svg.SvgPathImage
    image = qrcode.make(data, image_factory=factory, border=2, box_size=10)
    response = HttpResponse(content_type='image/svg+xml')
    image.save(response)
    return response


def _order_location_note(table, service_mode=Order.ServiceMode.DINE_IN, fulfillment_mode=None):
    if table or fulfillment_mode == Order.FulfillmentMode.TABLE:
        if table:
            return f'الطاولة: {table.name_ar}' + (f' — المساحة: {table.room.name_ar}' if table.room_id else '')
        return 'طاولة'
    if fulfillment_mode == Order.FulfillmentMode.DELIVERY:
        return 'توصيل'
    if fulfillment_mode == Order.FulfillmentMode.TAKEAWAY or service_mode == Order.ServiceMode.TAKEAWAY:
        return 'تيك أواي'
    return 'طلب داخل المكان'



def _parse_report_date(raw_date):
    if raw_date:
        try:
            return datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            pass
    return datetime.now(DAMASCUS_TZ).date()


def _validate_phone_input(raw_value, field_label, errors, required=False):
    value = (raw_value or '').strip()
    if not value:
        if required:
            errors[field_label] = f'{field_label} مطلوب.'
        return value
    if not PHONE_ALLOWED_PATTERN.match(value):
        errors[field_label] = f'{field_label} يجب أن يحتوي على أرقام فقط مع السماح بـ + والمسافات والشرطات والأقواس.'
    return value


def _day_range_utc(report_date):
    local_start = datetime.combine(report_date, time.min).replace(tzinfo=DAMASCUS_TZ)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(ZoneInfo('UTC')), local_end.astimezone(ZoneInfo('UTC'))


def _line_total_for_item(item):
    if item.line_total_syp_snapshot is not None:
        return item.line_total_syp_snapshot
    return item.quantity * item.unit_price_syp_snapshot


def _items_total(items):
    return sum(_line_total_for_item(item) for item in items)


def _subtotal_for_selected(selected):
    return sum(qty * max(product.price_syp + option_delta, 0) for product, qty, _item_note, _snapshot, option_delta in selected)


def _delivery_fee_for_settings(settings, fulfillment_mode, raw_manual_fee=None):
    if fulfillment_mode != Order.FulfillmentMode.DELIVERY:
        return 0
    if not settings or not settings.effective_delivery_enabled:
        return 0
    if settings.delivery_fee_mode == SystemSetting.DeliveryFeeMode.FIXED:
        return max(settings.fixed_delivery_fee_syp or 0, 0)
    if settings.delivery_fee_mode == SystemSetting.DeliveryFeeMode.MANUAL:
        return _parse_nonnegative_int(raw_manual_fee, default=0, maximum=10_000_000)
    return 0


def _validate_fulfillment_mode(request, settings, table=None, allow_table=True):
    requested = (request.POST.get('fulfillment_mode') or '').strip()
    if table:
        default_mode = Order.FulfillmentMode.TABLE
        mode = Order.FulfillmentMode.TABLE
    else:
        default_mode = getattr(settings, 'safe_default_fulfillment_mode', Order.FulfillmentMode.INSIDE_SPACE) or Order.FulfillmentMode.INSIDE_SPACE
        mode = requested or default_mode
    valid = set(Order.FulfillmentMode.values)
    if mode not in valid:
        return default_mode, 'وضع الطلب المحدد غير صالح.'
    if mode == Order.FulfillmentMode.TABLE and not allow_table:
        return default_mode, 'طلب الطاولة غير متاح من هذه الواجهة.'
    if mode == Order.FulfillmentMode.TABLE and not table:
        # Public menu without QR remains in-space unless a staff POS table is selected.
        return Order.FulfillmentMode.INSIDE_SPACE, ''
    if mode == Order.FulfillmentMode.DELIVERY and (not settings or not settings.effective_delivery_enabled):
        return default_mode, 'خيار التوصيل غير مفعّل حالياً.'
    if mode == Order.FulfillmentMode.TAKEAWAY and (not settings or not settings.effective_takeaway_enabled):
        return default_mode, 'خيار التيك أواي غير مفعّل حالياً.'
    return mode, ''


def _validate_delivery_details(request, settings, fulfillment_mode, subtotal, manual_fee_name='delivery_fee_syp'):
    errors = []
    delivery_data = {
        'delivery_customer_name': request.POST.get('delivery_customer_name', request.POST.get('customer_name', '')).strip(),
        'delivery_phone': request.POST.get('delivery_phone', request.POST.get('customer_phone', '')).strip(),
        'delivery_area': request.POST.get('delivery_area', '').strip(),
        'delivery_landmark': request.POST.get('delivery_landmark', '').strip(),
        'delivery_address': request.POST.get('delivery_address', '').strip(),
        'delivery_notes': request.POST.get('delivery_notes', '').strip(),
        'delivery_fee_syp': _delivery_fee_for_settings(settings, fulfillment_mode, request.POST.get(manual_fee_name)),
        'delivery_status': Order.DeliveryStatus.NOT_APPLICABLE,
    }
    if fulfillment_mode != Order.FulfillmentMode.DELIVERY:
        return delivery_data, errors
    delivery_data['delivery_status'] = Order.DeliveryStatus.NEW
    if settings.minimum_delivery_order_syp and subtotal < settings.minimum_delivery_order_syp:
        errors.append(f'الحد الأدنى لطلب التوصيل هو {settings.minimum_delivery_order_syp} ل.س.')
    if (settings.require_delivery_address or settings.require_address_for_delivery) and not delivery_data['delivery_address']:
        errors.append('عنوان التوصيل مطلوب.')
    if (settings.require_delivery_phone or settings.require_phone_for_delivery) and not delivery_data['delivery_phone']:
        errors.append('رقم الهاتف مطلوب للتوصيل.')
    return delivery_data, errors


def _paid_total(payments):
    return sum(payment.amount_syp for payment in payments if payment.is_active and not payment.is_reversed and payment.method != Payment.Method.UNPAID)


def _parse_positive_int(raw_value, default=1, maximum=99):
    try:
        value = int((raw_value or str(default)).strip())
    except (TypeError, ValueError):
        return default
    return min(max(value, 1), maximum)


def _parse_nonnegative_int(raw_value, default=0, maximum=None):
    try:
        value = int((raw_value or str(default)).strip())
    except (TypeError, ValueError):
        return default
    value = max(value, 0)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _build_day_report(report_date):
    start_utc, end_utc = _day_range_utc(report_date)
    orders = (
        Order.objects.select_related('table', 'table__room')
        .prefetch_related('items', 'payments')
        .filter(created_at__gte=start_utc, created_at__lt=end_utc)
        .order_by('-created_at')
    )
    rows = []
    sums = {
        'orders_count': 0, 'gross_sales': 0, 'net_sales': 0, 'order_total': 0, 'subtotal_total': 0, 'delivery_fee_total': 0, 'paid_total': 0, 'remaining_total': 0,
        'cash_total': 0, 'manual_transfer_total': 0, 'free_total': 0, 'discount_total': 0, 'discounts_syp': 0,
        'cancelled_count': 0, 'cancelled_value': 0, 'served_or_paid_count': 0, 'unpaid_orders_count': 0, 'partial_orders_count': 0, 'partial_payments_syp': 0, 'non_cash_sales_syp': 0,
        'delivery_order_count': 0, 'delivery_gross_sales': 0, 'delivery_fees_total': 0, 'unpaid_delivery_orders': 0, 'cancelled_delivery_orders': 0, 'takeaway_order_count': 0,
        'estimated_cost_syp': 0, 'estimated_gross_margin_syp': 0, 'margin_known_sales_syp': 0, 'products_missing_cost_count': 0, 'comp_value_syp': 0,
        'internet_workspace_sessions_count': 0, 'internet_workspace_raw_minutes': 0, 'internet_workspace_billable_minutes': 0, 'internet_workspace_revenue_syp': 0,
        'internet_workspace_prepaid_minutes_used': 0, 'internet_workspace_metered_revenue_syp': 0, 'internet_workspace_cancelled_sessions': 0, 'internet_workspace_unpaid_orders': 0,
    }
    for order in orders:
        total, paid, remaining, payment_label = _order_financials(order)
        methods = {}
        for payment in order.payments.all():
            methods[payment.method] = methods.get(payment.method, 0) + payment.amount_syp
        if order.is_delivery:
            sums['delivery_order_count'] += 1
            sums['delivery_gross_sales'] += total
            sums['delivery_fees_total'] += max(order.delivery_fee_syp or 0, 0)
            if remaining > 0:
                sums['unpaid_delivery_orders'] += 1
            if order.delivery_status == Order.DeliveryStatus.CANCELLED or order.status == Order.Status.CANCELLED:
                sums['cancelled_delivery_orders'] += 1
        if order.fulfillment_mode == Order.FulfillmentMode.TAKEAWAY:
            sums['takeaway_order_count'] += 1
        if order.status == Order.Status.CANCELLED:
            sums['cancelled_count'] += 1
            sums['cancelled_value'] += total
        if order.status == Order.Status.SERVED or remaining == 0:
            sums['served_or_paid_count'] += 1
        if remaining > 0:
            sums['unpaid_orders_count'] += 1
        if paid > 0 and remaining > 0:
            sums['partial_orders_count'] += 1
            sums['partial_payments_syp'] += remaining
        sums['orders_count'] += 1
        sums['gross_sales'] += order.subtotal_syp + order.service_fee_syp + max(order.delivery_fee_syp or 0, 0)
        sums['discounts_syp'] += order.discount_syp
        sums['comp_value_syp'] += sum(d.amount_syp for d in order.discounts.all() if d.is_active and d.discount_type == OrderDiscount.DiscountType.COMP)
        for item in order.items.all():
            im = item_margin(item, allow_current_product_cost=True)
            if im['estimated_cost_syp'] is None:
                sums['products_missing_cost_count'] += 1
            else:
                sums['estimated_cost_syp'] += im['estimated_cost_syp']
                sums['estimated_gross_margin_syp'] += im['estimated_margin_syp']
                sums['margin_known_sales_syp'] += im['revenue_syp']
        sums['net_sales'] += total
        sums['order_total'] += total
        sums['subtotal_total'] += order.subtotal_syp
        sums['delivery_fee_total'] += max(order.delivery_fee_syp or 0, 0)
        sums['paid_total'] += paid
        sums['remaining_total'] += remaining
        sums['cash_total'] += methods.get(Payment.Method.CASH, 0)
        sums['manual_transfer_total'] += methods.get(Payment.Method.MANUAL_TRANSFER, 0)
        sums['free_total'] += methods.get(Payment.Method.FREE, 0)
        sums['discount_total'] += methods.get(Payment.Method.MEMBER_DISCOUNT, 0)
        sums['non_cash_sales_syp'] += methods.get(Payment.Method.MANUAL_TRANSFER, 0) + methods.get(Payment.Method.FREE, 0) + methods.get(Payment.Method.MEMBER_DISCOUNT, 0)
        rows.append({'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label, 'methods': methods})
    sessions = InternetSession.objects.select_related('linked_order').filter(started_at__gte=start_utc, started_at__lt=end_utc)
    for sess in sessions:
        sums['internet_workspace_sessions_count'] += 1
        sums['internet_workspace_raw_minutes'] += sess.effective_duration_minutes or 0
        sums['internet_workspace_billable_minutes'] += sess.billable_minutes or 0
        sums['internet_workspace_revenue_syp'] += sess.payable_total_syp or 0
        sums['internet_workspace_prepaid_minutes_used'] += sess.member_minutes_used or 0
        if sess.billing_mode == InternetSession.BillingMode.OPEN_METERED:
            sums['internet_workspace_metered_revenue_syp'] += sess.payable_total_syp or 0
        if sess.status == InternetSession.Status.CANCELLED:
            sums['internet_workspace_cancelled_sessions'] += 1
        if sess.linked_order_id and sess.status in {InternetSession.Status.UNPAID, InternetSession.Status.BILLED}:
            sums['internet_workspace_unpaid_orders'] += 1
    from core.finance import finance_summary_for_date
    finance = finance_summary_for_date(report_date, sums)
    sums.update({k: v for k, v in finance.items() if not hasattr(v, 'model')})
    sums['expected_cash_syp'] = finance['expected_cash_syp']
    sums['avg_order_value'] = int(sums['order_total'] / sums['orders_count']) if sums['orders_count'] else 0
    sums['average_delivery_order_value'] = int(sums['delivery_gross_sales'] / sums['delivery_order_count']) if sums['delivery_order_count'] else 0
    sums['estimated_gross_margin_percent'] = round((sums['estimated_gross_margin_syp'] / sums['margin_known_sales_syp']) * 100, 2) if sums['margin_known_sales_syp'] else None
    return rows, sums

def dashboard(request):
    return render(request, 'core/dashboard.html')


def _option_assignment_prefetch():
    return Prefetch(
        'option_group_assignments',
        queryset=ProductOptionGroupAssignment.objects.filter(
            is_active=True,
            group__is_active=True,
        ).select_related('group').prefetch_related(
            Prefetch('group__options', queryset=ProductOption.objects.filter(is_active=True).order_by('sort_order', 'name_ar'))
        ).order_by('sort_order', 'group__sort_order', 'group__name_ar'),
        to_attr='active_option_assignments',
    )


def _valid_option_assignments_for_product(product):
    return [
        assignment
        for assignment in getattr(product, 'active_option_assignments', [])
        if assignment.group.applies_to_product(product) and list(assignment.group.options.all())
    ]


def _active_media_prefetch(**media_filters):
    filters = {'is_active': True}
    filters.update(media_filters)
    return Prefetch(
        'media',
        queryset=ProductMedia.objects.filter(**filters).select_related('media_asset').order_by('-is_primary', 'sort_order', 'created_at'),
        to_attr='active_media',
    )


def _primary_media_for_product(product):
    active_media = getattr(product, 'active_media', [])
    return active_media[0] if active_media else None


def _attach_product_card_data(products):
    for product in products:
        product.valid_option_assignments = _valid_option_assignments_for_product(product)
        product.primary_media = _primary_media_for_product(product)
    return products


def _attach_valid_option_assignments(products):
    return _attach_product_card_data(products)


def _section_products_for_ordering(product_filter=None, section_filter=None, include_unsectioned=False, media_filter=None):
    if product_filter is None:
        product_filter = {}
    if section_filter is None:
        section_filter = {}
    products_qs = Product.objects.filter(**product_filter).prefetch_related(_option_assignment_prefetch(), _active_media_prefetch(**(media_filter or {}))).order_by('sort_order', 'name_ar')
    sections = (
        MenuSection.objects.filter(is_active=True, **section_filter)
        .prefetch_related(Prefetch('products', queryset=products_qs))
        .order_by('sort_order', 'name_ar')
    )
    section_products = []
    product_ids_in_sections = set()
    for section in sections:
        products = _attach_valid_option_assignments(list(section.products.all()))
        if products:
            product_ids_in_sections.update(product.id for product in products)
            section_products.append((section, products))
    if include_unsectioned:
        unsectioned_products = _attach_valid_option_assignments(list(products_qs.exclude(pk__in=product_ids_in_sections)))
        if unsectioned_products:
            section_products.append((None, unsectioned_products))
    return section_products


def _menu_context(table=None, request=None):
    settings = get_system_settings()
    section_products = _section_products_for_ordering(
        product_filter={'is_available': True, 'visible_on_qr': True},
        section_filter={'visible_on_qr': True},
        media_filter={'display_on_public_menu': True},
    )
    page = get_page_setting('public_menu', 'القائمة العامة', 'Public menu', 'اختر طلبك داخل المكان.', 'Choose your in-space order.')
    default_fulfillment_mode = Order.FulfillmentMode.TABLE if table else Order.FulfillmentMode.INSIDE_SPACE
    member_context = resolve_member_from_request(request) if request else None
    if member_context:
        for _section, products in section_products:
            for product in products:
                result = evaluate_membership_benefit(member_context, product)
                product.member_price_syp = result.final_total
                product.member_discount_syp = result.discount
    return {'table': table, 'section_products': section_products, 'settings': settings, 'page_setting': page, 'default_fulfillment_mode': default_fulfillment_mode, 'fulfillment_choices': settings.available_fulfillment_modes(include_table=False), 'member_context': member_context}


def menu_public(request):
    if request.method == 'POST':
        return _create_order_from_menu(request, table=None)
    return render(request, 'menu/menu.html', _menu_context(request=request))


def menu_table(request, qr_token):
    table = get_object_or_404(TableArea.objects.select_related('room'), qr_token=qr_token)
    if request.method == 'POST':
        return _create_order_from_menu(request, table=table)
    return render(request, 'menu/menu.html', _menu_context(table=table, request=request))


def _selected_option_values(request, product_id, group_id):
    base_name = f'option_{product_id}_{group_id}'
    return [value for value in request.POST.getlist(base_name) + request.POST.getlist(f'{base_name}[]') if value]


def _posted_option_group_ids(request, product_id):
    prefix = f'option_{product_id}_'
    group_ids = set()
    for key in request.POST.keys():
        if not key.startswith(prefix):
            continue
        raw_group_id = key.removeprefix(prefix).removesuffix('[]')
        if raw_group_id.isdigit() and any(request.POST.getlist(key)):
            group_ids.add(raw_group_id)
    return group_ids


def _validate_product_options(request, product):
    assignments = _valid_option_assignments_for_product(product)
    selected_snapshot = []
    total_delta = 0
    errors = []
    allowed_group_ids = {str(assignment.group_id) for assignment in assignments}
    invalid_posted_group_ids = _posted_option_group_ids(request, product.id) - allowed_group_ids
    if invalid_posted_group_ids:
        errors.append(f'تم إرسال خيارات غير صالحة للعنصر {product.name_ar}.')

    for assignment in assignments:
        group = assignment.group
        active_options = list(group.options.all())
        option_by_id = {str(option.id): option for option in active_options}
        raw_values = _selected_option_values(request, product.id, group.id)
        if group.selection_type == ProductOptionGroup.SelectionType.SINGLE and len(raw_values) > 1:
            errors.append(f'اختر خياراً واحداً فقط من {group.name_ar} للعنصر {product.name_ar}.')
            continue

        selected_options = []
        invalid_selection = False
        seen = set()
        for raw_value in raw_values:
            if raw_value in seen:
                continue
            seen.add(raw_value)
            option = option_by_id.get(str(raw_value))
            if not option:
                invalid_selection = True
                continue
            selected_options.append(option)

        if invalid_selection:
            errors.append(f'تم اختيار خيار غير متاح للعنصر {product.name_ar}.')
            continue

        selected_count = len(selected_options)
        min_required = max(group.min_selected or 0, 1 if group.is_required else 0)
        if selected_count < min_required:
            errors.append(f'يرجى اختيار {min_required} خيار على الأقل من {group.name_ar} للعنصر {product.name_ar}.')
            continue
        if group.max_selected is not None and selected_count > group.max_selected:
            errors.append(f'يمكن اختيار {group.max_selected} خيار كحد أقصى من {group.name_ar} للعنصر {product.name_ar}.')
            continue
        if group.selection_type == ProductOptionGroup.SelectionType.SINGLE and selected_count > 1:
            errors.append(f'اختر خياراً واحداً فقط من {group.name_ar} للعنصر {product.name_ar}.')
            continue

        if selected_options:
            total_delta += sum(option.price_delta_syp for option in selected_options)
            selected_snapshot.append({
                'group_id': group.id,
                'group_name_ar': group.name_ar,
                'group_name_en': group.name_en,
                'selection_type': group.selection_type,
                'options': [
                    {
                        'option_id': option.id,
                        'name_ar': option.name_ar,
                        'name_en': option.name_en,
                        'price_delta_syp': option.price_delta_syp,
                    }
                    for option in selected_options
                ],
            })

    return selected_snapshot, total_delta, errors


def _selected_order_items_from_post(request, products):
    selected = []
    validation_errors = []
    for product in products:
        qty_raw = request.POST.get(f'qty_{product.id}', '').strip()
        if not qty_raw:
            continue
        try:
            qty = int(qty_raw)
        except ValueError:
            continue
        if qty <= 0:
            continue
        item_note = request.POST.get(f'note_{product.id}', '').strip()
        selected_options_snapshot, option_delta, option_errors = _validate_product_options(request, product)
        validation_errors.extend(option_errors)
        selected.append((product, qty, item_note, selected_options_snapshot, option_delta))
    return selected, validation_errors




def _prep_defaults_for_product(product):
    requires_prep = product.infer_requires_preparation() if hasattr(product, 'infer_requires_preparation') else True
    if not requires_prep:
        return None, OrderItem.PrepStatus.NO_PREP
    station = getattr(product, 'prep_station_ref', None)
    if station is None:
        try:
            station = PrepStation.objects.filter(code='general', is_active=True).first() or PrepStation.objects.filter(station_type='general', is_active=True).first()
        except Exception:
            station = None
    return station, OrderItem.PrepStatus.NEW

def _create_order_from_selected_items(table, selected, note_parts, status=None, service_mode=None, fulfillment_mode=None, delivery_data=None, member_context=None):
    note = '\n'.join([part for part in note_parts if part])
    if table and service_mode is None:
        service_mode = Order.ServiceMode.TABLE
    service_mode = service_mode or Order.ServiceMode.DINE_IN
    fulfillment_mode = fulfillment_mode or (Order.FulfillmentMode.TABLE if table else Order.FulfillmentMode.INSIDE_SPACE)
    delivery_data = delivery_data or {}
    with transaction.atomic():
        order = Order.objects.create(table=table, member=member_context.member if member_context else None, service_mode=service_mode, fulfillment_mode=fulfillment_mode, status=status or Order.Status.NEW, notes=note, **delivery_data)
        membership_discount = 0
        benefit_snapshots = []
        for product, qty, item_note, selected_options_snapshot, option_delta in selected:
            unit_price = max(product.price_syp + option_delta, 0)
            prep_station, prep_status = _prep_defaults_for_product(product)
            item = OrderItem.objects.create(
                order=order,
                product=product,
                quantity=qty,
                product_name_ar_snapshot=product.name_ar,
                product_name_en_snapshot=product.name_en,
                unit_price_syp_snapshot=unit_price,
                selected_options_snapshot=selected_options_snapshot,
                item_note=item_note,
                line_total_syp_snapshot=qty * unit_price,
                prep_station=prep_station,
                prep_status=prep_status,
            )
            benefit = evaluate_membership_benefit(member_context, product, qty, unit_price)
            if benefit.discount:
                membership_discount += benefit.discount
                benefit_snapshots.append({'rule_uuid': str(benefit.rule.uuid), 'label': str(benefit.rule), 'product': product.name_ar, 'subtotal_before': benefit.original_total, 'discount': benefit.discount, 'subtotal_after': benefit.final_total, 'plan': member_context.plan.name_ar})
            transaction.on_commit(lambda item_id=item.pk: deduct_order_item_stock(OrderItem.objects.select_related('order','product').get(pk=item_id)))
        if membership_discount:
            import json
            OrderDiscount.objects.create(order=order, discount_type=OrderDiscount.DiscountType.MEMBER, amount_syp=membership_discount, reason='خصم العضوية', notes=json.dumps(benefit_snapshots, ensure_ascii=False))
    transaction.on_commit(lambda: notify_order_created(order))
    return order


def _create_order_from_menu(request, table=None):
    products = _attach_valid_option_assignments(list(Product.objects.filter(is_available=True, visible_on_qr=True, orderable_on_qr=True).prefetch_related(_option_assignment_prefetch())))
    selected, validation_errors = _selected_order_items_from_post(request, products)

    if not selected:
        context = _menu_context(table, request)
        context['error'] = 'يرجى اختيار عنصر واحد على الأقل.'
        context['form_values'] = request.POST
        return render(request, 'menu/menu.html', context)

    settings = get_system_settings()
    fulfillment_mode, fulfillment_error = _validate_fulfillment_mode(request, settings, table=table, allow_table=bool(table))
    if fulfillment_error:
        validation_errors.append(fulfillment_error)
    subtotal = _subtotal_for_selected(selected)
    delivery_data, delivery_errors = _validate_delivery_details(request, settings, fulfillment_mode, subtotal)
    validation_errors.extend(delivery_errors)
    customer_name = request.POST.get('customer_name', '').strip()
    errors = {}
    customer_phone = _validate_phone_input(request.POST.get('customer_phone', ''), 'رقم الهاتف', errors, required=fulfillment_mode == Order.FulfillmentMode.DELIVERY and (settings.require_delivery_phone or settings.require_phone_for_delivery))
    if errors:
        validation_errors.append(errors['رقم الهاتف'])
    if validation_errors:
        context = _menu_context(table, request)
        context['error'] = ' '.join(validation_errors)
        context['form_values'] = request.POST
        return render(request, 'menu/menu.html', context)
    general_note = request.POST.get('general_note', '').strip()
    service_mode = Order.ServiceMode.TABLE if fulfillment_mode == Order.FulfillmentMode.TABLE else (Order.ServiceMode.TAKEAWAY if fulfillment_mode == Order.FulfillmentMode.TAKEAWAY else Order.ServiceMode.DINE_IN)
    note_parts = [f'الاسم: {customer_name}' if customer_name else '', f'الهاتف: {customer_phone}' if customer_phone else '', f'المكان: {_order_location_note(table, service_mode, fulfillment_mode)}', general_note]
    member_context = resolve_member_from_request(request)
    order = _create_order_from_selected_items(table, selected, note_parts, service_mode=service_mode, fulfillment_mode=fulfillment_mode, delivery_data=delivery_data, member_context=member_context)
    return redirect(reverse('order_public', kwargs={'public_code': order.public_code}))

def order_public(request, public_code):
    try:
        order = Order.objects.select_related('table', 'table__room').prefetch_related('items__product').get(public_code=public_code)
    except Order.DoesNotExist as exc:
        raise Http404('الطلب غير موجود') from exc
    total = order.total_with_delivery_syp if order.is_delivery else max(order.subtotal_syp + order.service_fee_syp - order.discount_syp, 0)
    needs_confirmation = any(item.product.requires_staff_confirmation for item in order.items.all() if item.product_id)
    qr_url = reverse('order_qr', kwargs={'public_code': order.public_code})
    return render(request, 'menu/order_confirm.html', {'order': order, 'subtotal': order.subtotal_syp, 'total': total, 'needs_confirmation': needs_confirmation, 'qr_url': qr_url})


def order_qr(request, public_code):
    order = get_object_or_404(Order, public_code=public_code)
    path = reverse('order_public', kwargs={'public_code': order.public_code})
    return _qr_svg_response(request.build_absolute_uri(path))


STAFF_CAPABILITIES = {
    'reporting/close-day': {'admin'},
    'cashier/payments': {'admin', 'cashier'},
    'reservations': {'admin', 'waiter'},
    'events': {'admin', 'waiter'},
    'vendors': {'admin'},
    'members/internet': {'admin', 'cashier'},
    'operations': {'admin', 'cashier', 'waiter', 'kitchen'},
}


LEGACY_CAPABILITY_MAP = {
    'reporting/close-day': 'reports',
    'cashier/payments': 'cashier',
    'operations': 'staff_home',
    'pos': 'pos',
    'orders': 'orders',
    'order_edit': 'order_edit',
    'modifiers': 'modifiers',
}


def _can_approve_partial_payment(user):
    return can_approve_partial_payment(user)


def _assert_staff_capability(user, capability_name):
    capability = LEGACY_CAPABILITY_MAP.get(capability_name)
    if capability:
        allowed = user_has_capability(user, capability)
    else:
        allowed = bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_superuser or getattr(user, 'role', '') in STAFF_CAPABILITIES.get(capability_name, set()))
        )
    if allowed:
        return
    ActivityLog.objects.create(
        action='staff_access_denied',
        details=f'capability={capability or capability_name}; user={getattr(user, "pk", None)}; role={getattr(user, "role", "")}',
    )
    raise PermissionDenied(ACCESS_DENIED_MESSAGE)

def _order_financials(order):
    total = order.total_syp
    paid = order.paid_syp
    remaining = order.remaining_syp
    if order.status == Order.Status.CANCELLED:
        payment_label = 'ملغى'
    elif total == 0:
        payment_label = 'ضيافة'
    elif remaining == 0:
        payment_label = 'مدفوع'
    elif paid > 0:
        payment_label = 'مدفوع جزئياً'
    else:
        payment_label = 'غير مدفوع'
    return total, paid, remaining, payment_label


def _order_edit_block_reason(order):
    total, paid, remaining, _payment_label = _order_financials(order)
    if order.status == Order.Status.CANCELLED:
        return 'لا يمكن تعديل طلب ملغى.', total, paid, remaining
    if total > 0 and remaining == 0:
        return 'لا يمكن تعديل طلب مدفوع بالكامل.', total, paid, remaining
    return '', total, paid, remaining


def _snapshot_option_delta(selected_options_snapshot):
    total = 0
    for group in selected_options_snapshot or []:
        for option in group.get('options', []):
            try:
                total += int(option.get('price_delta_syp') or 0)
            except (TypeError, ValueError):
                continue
    return total


def _selected_snapshot_option_ids(selected_options_snapshot):
    ids = set()
    for group in selected_options_snapshot or []:
        for option in group.get('options', []):
            option_id = option.get('option_id')
            if option_id is not None:
                ids.add(str(option_id))
    return ids


def _order_edit_context(order, error=''):
    items = list(order.items.select_related('product').all())
    products = _attach_valid_option_assignments(
        list(Product.objects.filter(is_available=True).prefetch_related(_option_assignment_prefetch()).order_by('sort_order', 'name_ar'))
    )
    product_map = {product.id: product for product in products}
    item_rows = []
    for item in items:
        product = product_map.get(item.product_id)
        if product is None and item.product_id:
            product = Product.objects.filter(pk=item.product_id).prefetch_related(_option_assignment_prefetch()).first()
            if product:
                _attach_valid_option_assignments([product])
        item_rows.append({
            'item': item,
            'line_total': _line_total_for_item(item),
            'product': product,
            'selected_option_ids': _selected_snapshot_option_ids(item.selected_options_snapshot),
        })
    total, paid, remaining, payment_label = _order_financials(order)
    return {
        'order': order,
        'item_rows': item_rows,
        'products': products,
        'total': total,
        'paid': paid,
        'remaining': remaining,
        'payment_label': payment_label,
        'error': error,
        'can_edit': not error,
    }


def _log_order_edit(user, action, order, details=None):
    payload = {'order_public_code': str(order.public_code)}
    if details:
        payload.update(details)
    ActivityLog.objects.create(actor=user, action=action, details=payload)


def _redirect_if_order_edit_blocked(request, order):
    reason, _total, _paid, _remaining = _order_edit_block_reason(order)
    if reason:
        messages.error(request, reason)
        return redirect('staff_order_edit', public_code=order.public_code)
    return None


@require_staff_capability('staff_home')
def staff_home(request):
    return render(request, 'staff/home.html')


@require_staff_capability('pos')
def staff_pos(request):
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    section_products = _section_products_for_ordering(product_filter={'is_available': True, 'visible_on_pos': True, 'orderable_on_pos': True}, include_unsectioned=True, media_filter={'display_on_pos': True})
    products = [product for _section, products in section_products for product in products]
    member_query = request.GET.get('member_q', '').strip()
    member_rows = []
    if member_query:
        member_rows = list(
            Member.objects.filter(Q(name_ar__icontains=member_query) | Q(phone__icontains=member_query))
            .order_by('-created_at')[:8]
        )

    settings = get_system_settings()
    context = {
        'section_products': section_products,
        'tables': tables,
        'member_query': member_query,
        'member_rows': member_rows,
        'settings': settings,
        'page_setting': get_page_setting('staff_pos', 'نقطة البيع', 'POS', 'إدخال طلبات داخل المكان أو على الطاولات.', 'Create table or in-space orders.'),
    }
    if request.method == 'POST':
        selected, validation_errors = _selected_order_items_from_post(request, products)
        table = None
        table_id = request.POST.get('table_id', '').strip()
        if table_id and not table_id.isdigit():
            validation_errors.append('الطاولة المحددة غير صالحة.')
        elif table_id:
            table = TableArea.objects.filter(pk=int(table_id)).first()
            if table is None:
                validation_errors.append('الطاولة المحددة غير صالحة.')
        fulfillment_mode, fulfillment_error = _validate_fulfillment_mode(request, settings, table=table, allow_table=bool(table))
        if table:
            fulfillment_mode = Order.FulfillmentMode.TABLE
        if fulfillment_error:
            validation_errors.append(fulfillment_error)
        if request.POST.get('fulfillment_mode') == Order.FulfillmentMode.TABLE and not table:
            validation_errors.append('يرجى اختيار طاولة لهذا الطلب.')
        if not selected:
            context.update({'error': 'يرجى اختيار عنصر واحد على الأقل.', 'form_values': request.POST, 'selected_table_id': table_id})
            return render(request, 'staff/pos.html', context)

        subtotal = _subtotal_for_selected(selected)
        delivery_data, delivery_errors = _validate_delivery_details(request, settings, fulfillment_mode, subtotal)
        validation_errors.extend(delivery_errors)
        errors = {}
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = _validate_phone_input(request.POST.get('customer_phone', ''), 'رقم الهاتف', errors, required=fulfillment_mode == Order.FulfillmentMode.DELIVERY and (settings.require_delivery_phone or settings.require_phone_for_delivery))
        if errors:
            validation_errors.append(errors['رقم الهاتف'])
        member_id = request.POST.get('member_id', '').strip()
        member = None
        if member_id and member_id.isdigit():
            member = Member.objects.filter(pk=int(member_id)).first()
        if member_id and not member:
            validation_errors.append('العضو المحدد غير صالح.')
        member_context = get_active_member_context(member) if member else None
        if validation_errors:
            context.update({'error': ' '.join(validation_errors), 'form_values': request.POST, 'selected_table_id': table_id})
            return render(request, 'staff/pos.html', context)

        general_note = request.POST.get('general_note', '').strip()
        service_mode = Order.ServiceMode.TABLE if fulfillment_mode == Order.FulfillmentMode.TABLE else (Order.ServiceMode.TAKEAWAY if fulfillment_mode == Order.FulfillmentMode.TAKEAWAY else Order.ServiceMode.DINE_IN)
        table_label = _order_location_note(table, service_mode, fulfillment_mode)
        note_parts = [
            'Source: staff/pos',
            f'المكان: {table_label}',
            f'الاسم: {customer_name}' if customer_name else '',
            f'الهاتف: {customer_phone}' if customer_phone else '',
            f'العضو: {member.name_ar} / {member.phone}' if member else '',
            general_note,
        ]
        order = _create_order_from_selected_items(table, selected, note_parts, status=Order.Status.NEW, service_mode=service_mode, fulfillment_mode=fulfillment_mode, delivery_data=delivery_data, member_context=member_context)
        ActivityLog.objects.create(
            actor=request.user,
            action='staff_pos_order_created',
            details={'order_public_code': str(order.public_code), 'table_id': table.id if table else None, 'fulfillment_mode': fulfillment_mode},
        )
        return redirect('staff_cashier_order', public_code=order.public_code)

    return render(request, 'staff/pos.html', context)


@require_staff_capability('modifiers')
def staff_modifiers(request):
    groups = ProductOptionGroup.objects.prefetch_related(
        'options',
        Prefetch(
            'product_assignments',
            queryset=ProductOptionGroupAssignment.objects.select_related('product').order_by('product__name_ar', 'sort_order'),
        ),
    ).order_by('sort_order', 'name_ar')
    rows = []
    invalid_assignments = []
    for group in groups:
        assignments = list(group.product_assignments.all())
        invalid_for_group = [assignment for assignment in assignments if not group.applies_to_product(assignment.product)]
        invalid_assignments.extend(invalid_for_group)
        rows.append({'group': group, 'assignments': assignments, 'invalid_assignments': invalid_for_group})
    return render(request, 'staff/modifiers.html', {'rows': rows, 'invalid_assignments': invalid_assignments})


@login_required
def staff_qr_links(request):
    _assert_staff_capability(request.user, 'operations')
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    rows = []
    for table in tables:
        path = reverse('menu_table', kwargs={'qr_token': table.qr_token})
        rows.append({'table': table, 'path': path, 'full_url': request.build_absolute_uri(path), 'qr_svg_url': reverse('table_qr', kwargs={'qr_token': table.qr_token})})
    return render(request, 'staff/qr_links.html', {'rows': rows})


@login_required
def staff_qr_print(request):
    _assert_staff_capability(request.user, 'operations')
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    rows = []
    for table in tables:
        path = reverse('menu_table', kwargs={'qr_token': table.qr_token})
        rows.append({'table': table, 'full_url': request.build_absolute_uri(path), 'qr_svg_url': reverse('table_qr', kwargs={'qr_token': table.qr_token})})
    return render(request, 'staff/qr_print.html', {'rows': rows})


@login_required
def table_qr(request, qr_token):
    table = get_object_or_404(TableArea.objects.select_related('room'), qr_token=qr_token)
    path = reverse('menu_table', kwargs={'qr_token': table.qr_token})
    return _qr_svg_response(request.build_absolute_uri(path))



@login_required
def staff_menu_tools(request):
    _assert_staff_capability(request.user, 'operations')
    products = _staff_menu_tools_queryset(request.GET)
    context = _staff_menu_tools_context(request, products)
    return render(request, 'staff/menu_tools.html', context)


@login_required
def staff_menu_tools_preview(request):
    _assert_staff_capability(request.user, 'operations')
    if request.method != 'POST':
        return redirect('staff_menu_tools')
    selected = _dedupe_selected_products(request.POST.getlist('selected_products'))
    action = request.POST.get('bulk_action', '')
    action_value = request.POST.get('bulk_value', '')
    try:
        result = preview_product_bulk_action(selected, action, action_value)
    except BulkEditValidationError as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('staff_menu_tools')}?{request.POST.get('return_query', '')}")
    return render(request, 'staff/menu_tools_preview.html', {
        'result': result,
        'selected_products': selected,
        'bulk_action': action,
        'bulk_value': action_value,
        'return_query': request.POST.get('return_query', ''),
    })


@login_required
def staff_menu_tools_apply(request):
    _assert_staff_capability(request.user, 'operations')
    if request.method != 'POST':
        return redirect('staff_menu_tools')
    if request.POST.get('confirm') != 'yes':
        messages.error(request, 'يجب تأكيد الإجراء قبل تطبيق التغييرات.')
        return redirect('staff_menu_tools')
    selected = _dedupe_selected_products(request.POST.getlist('selected_products'))
    action = request.POST.get('bulk_action', '')
    action_value = request.POST.get('bulk_value', '')
    return_query = request.POST.get('return_query', '')
    try:
        result = apply_product_bulk_action(selected, action, action_value, actor=request.user)
    except BulkEditValidationError as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('staff_menu_tools')}?{return_query}")
    changed_count = sum(1 for change in result.changes if change.changes)
    messages.success(request, f'تم تطبيق الإجراء على {result.matched_count} منتج. المنتجات المتغيرة فعلياً: {changed_count}.')
    return redirect(f"{reverse('staff_menu_tools')}?{return_query}")


def _dedupe_selected_products(selected):
    deduped = []
    seen = set()
    for value in selected:
        text = str(value).strip()
        if text and text not in seen:
            deduped.append(text)
            seen.add(text)
    return deduped


def _staff_menu_tools_queryset(params):
    active_visual_media = ProductMedia.objects.filter(
        product_id=OuterRef('pk'),
        is_active=True,
        media_type__in=[ProductMedia.MediaType.IMAGE, ProductMedia.MediaType.GIF, ProductMedia.MediaType.EXTERNAL_URL],
        display_on_public_menu=True,
    )
    products = Product.objects.select_related('category', 'prep_station_ref', 'vendor').prefetch_related('menu_sections', 'tags').annotate(
        has_active_image=Exists(active_visual_media)
    )
    query = params.get('q', '').strip()
    if query:
        products = products.filter(Q(name_ar__icontains=query) | Q(name_en__icontains=query))
    section = params.get('section', '').strip()
    if section:
        if section == 'none':
            products = products.filter(menu_sections__isnull=True)
        elif section.isdigit():
            products = products.filter(menu_sections__pk=section)
    availability = params.get('availability', '').strip()
    if availability in {'available', 'unavailable'}:
        products = products.filter(is_available=(availability == 'available'))
    qr_visibility = params.get('qr_visibility', '').strip()
    if qr_visibility in {'visible', 'hidden'}:
        products = products.filter(visible_on_qr=(qr_visibility == 'visible'))
    qr_orderability = params.get('qr_orderability', '').strip()
    if qr_orderability in {'orderable', 'not_orderable'}:
        products = products.filter(orderable_on_qr=(qr_orderability == 'orderable'))
    item_type = params.get('item_type', '').strip()
    if item_type:
        products = products.filter(item_type=item_type)
    prep_station = params.get('prep_station', '').strip()
    if prep_station:
        if prep_station == 'none':
            products = products.filter(prep_station_ref__isnull=True)
        elif prep_station.isdigit():
            products = products.filter(prep_station_ref_id=prep_station)
    vendor = params.get('vendor', '').strip()
    if vendor:
        if vendor == 'none':
            products = products.filter(vendor__isnull=True)
        elif vendor.isdigit():
            products = products.filter(vendor_id=vendor)
    tag = params.get('tag', '').strip()
    if tag:
        if tag == 'none':
            products = products.filter(tags__isnull=True)
        elif tag.isdigit():
            products = products.filter(tags__pk=tag)
    missing_image = params.get('missing_image', '').strip()
    if missing_image == 'yes':
        products = products.filter(has_active_image=False)
    elif missing_image == 'no':
        products = products.filter(has_active_image=True)
    return products.distinct().order_by('sort_order', 'name_ar')


def _staff_menu_tools_context(request, products):
    sections = MenuSection.objects.filter(is_active=True).order_by('sort_order', 'name_ar')
    grouped_map = {section.pk: {'section_name': section.name_ar, 'products': []} for section in sections}
    ungrouped = {'section_name': 'بدون قسم في المنيو', 'products': []}
    for product in products:
        product_sections = list(product.menu_sections.all())
        if product_sections:
            primary_section = sorted(product_sections, key=lambda section: (section.sort_order, section.name_ar))[0]
            grouped_map.setdefault(primary_section.pk, {'section_name': primary_section.name_ar, 'products': []})['products'].append(product)
        else:
            ungrouped['products'].append(product)
    grouped = [group for group in grouped_map.values() if group['products']]
    if ungrouped['products']:
        grouped.append(ungrouped)
    if not grouped:
        grouped = [{'section_name': 'لا توجد نتائج مطابقة', 'products': []}]
    return {
        'grouped': grouped,
        'products_count': products.count(),
        'sections': sections,
        'prep_stations': PrepStation.objects.filter(is_active=True).order_by('sort_order', 'name_ar'),
        'vendors': Vendor.objects.filter(is_active=True).order_by('name_ar'),
        'tags': Tag.objects.filter(is_active=True).order_by('name_ar'),
        'item_type_choices': Product.ItemType.choices,
        'bulk_actions': ALLOWED_ACTIONS.items(),
        'filters': request.GET,
        'return_query': request.GET.urlencode(),
    }


@require_staff_capability('orders')
def staff_orders(request):
    statuses = [choice[0] for choice in Order.Status.choices]
    orders = (
        Order.objects.select_related('table', 'table__room')
        .prefetch_related('items', 'payments')
        .order_by('-created_at')
    )
    grouped = []
    for status in statuses:
        rows = []
        for order in orders:
            if order.status != status:
                continue
            total, paid, remaining, payment_label = _order_financials(order)
            rows.append({'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label})
        grouped.append((status, Order.Status(status).label, rows))
    is_htmx = request.headers.get("HX-Request") == "true"
    template = 'staff/orders_partial.html' if is_htmx else 'staff/orders.html'
    return render(request, template, {'grouped': grouped, 'page_setting': get_page_setting('staff_orders', 'لوحة الطلبات', 'Orders')})


@require_staff_capability('orders')
def staff_order_status(request, public_code):
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order, public_code=public_code)
    correction = request.POST.get('action') == 'correct'
    reason = request.POST.get('reason', '').strip()
    new_delivery_status = request.POST.get('delivery_status')
    if new_delivery_status:
        if not user_has_capability(request.user, 'delivery_management'):
            messages.error(request, ACCESS_DENIED_MESSAGE)
            return redirect('staff_orders')
        if order.is_delivery:
            old_delivery_status = order.delivery_status
            try:
                if correction:
                    order = Order.correct_delivery_status(order.pk, actor=request.user, new_status=new_delivery_status, reason=reason)
                else:
                    order = Order.transition_delivery_status(
                        order.pk, actor=request.user, new_status=new_delivery_status,
                        cancellation_reason=request.POST.get('cancellation_reason', ''),
                        cancellation_notes=request.POST.get('cancellation_notes', ''),
                    )
                event_map = {
                    Order.DeliveryStatus.CONFIRMED: 'delivery_order_confirmed',
                    Order.DeliveryStatus.READY_FOR_DELIVERY: 'delivery_ready_for_delivery',
                    Order.DeliveryStatus.OUT_FOR_DELIVERY: 'delivery_out_for_delivery',
                    Order.DeliveryStatus.DELIVERED: 'delivery_delivered',
                    Order.DeliveryStatus.CANCELLED: 'delivery_cancelled',
                }
                if new_delivery_status in event_map:
                    create_notification(event_map[new_delivery_status], f'{order.get_delivery_status_display()} {order.display_number}', order.location_label, order=order, created_by=request.user)
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
        return redirect(request.POST.get('next') or 'staff_orders')
    new_status = request.POST.get('status')
    valid = {choice[0] for choice in Order.Status.choices}
    if new_status not in valid:
        return redirect('staff_orders')
    if order.status != new_status:
        if new_status == Order.Status.CANCELLED and not correction:
            cancellation_reason = request.POST.get('cancellation_reason', '').strip()
            _total, paid, _remaining, _label = _order_financials(Order.objects.prefetch_related('items', 'payments', 'discounts').get(pk=order.pk))
            if paid > 0 and not _can_approve_partial_payment(request.user):
                messages.error(request, 'إلغاء طلب مدفوع أو مدفوع جزئياً يحتاج موافقة المدير.')
                return redirect('staff_orders')
        try:
            if correction:
                Order.correct_status(order.pk, actor=request.user, new_status=new_status, reason=reason)
            else:
                Order.transition_status(order.pk, actor=request.user, new_status=new_status,
                                        cancellation_reason=request.POST.get('cancellation_reason', ''),
                                        cancellation_notes=request.POST.get('cancellation_notes', ''))
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
    return redirect('staff_orders')


@require_staff_capability('order_edit')
def staff_order_edit(request, public_code):
    order = get_object_or_404(
        Order.objects.select_related('table', 'table__room').prefetch_related('items__product', 'payments'),
        public_code=public_code,
    )
    block_reason, _total, _paid, _remaining = _order_edit_block_reason(order)
    return render(request, 'staff/order_edit.html', _order_edit_context(order, error=block_reason))


@require_staff_capability('order_edit')
def staff_order_edit_add_item(request, public_code):
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order.objects.prefetch_related('items', 'payments'), public_code=public_code)
    blocked = _redirect_if_order_edit_blocked(request, order)
    if blocked:
        return blocked

    product_id = request.POST.get('product_id', '').strip()
    if not product_id.isdigit():
        messages.error(request, 'المنتج المحدد غير صالح.')
        return redirect('staff_order_edit', public_code=order.public_code)
    product = get_object_or_404(
        Product.objects.filter(is_available=True, visible_on_pos=True, orderable_on_pos=True).prefetch_related(_option_assignment_prefetch()),
        pk=int(product_id),
    )
    _attach_valid_option_assignments([product])
    qty = _parse_positive_int(request.POST.get('quantity'), default=1)
    item_note = request.POST.get('item_note', '').strip()
    selected_options_snapshot, option_delta, option_errors = _validate_product_options(request, product)
    if option_errors:
        messages.error(request, ' '.join(option_errors))
        return redirect('staff_order_edit', public_code=order.public_code)

    unit_price = max(product.price_syp + option_delta, 0)
    prep_station, prep_status = _prep_defaults_for_product(product)
    with transaction.atomic():
        item = OrderItem.objects.create(
            order=order,
            product=product,
            quantity=qty,
            product_name_ar_snapshot=product.name_ar,
            product_name_en_snapshot=product.name_en,
            unit_price_syp_snapshot=unit_price,
            selected_options_snapshot=selected_options_snapshot,
            item_note=item_note,
            line_total_syp_snapshot=qty * unit_price,
            prep_station=prep_station,
            prep_status=prep_status,
        )
        transaction.on_commit(lambda item_id=item.pk: deduct_order_item_stock(OrderItem.objects.select_related('order','product').get(pk=item_id), request.user))
        _log_order_edit(request.user, 'order_item_added', order, {'item_id': item.id, 'product_id': product.id, 'quantity': qty})
        transaction.on_commit(lambda: create_notification('order_edited', f'تم تعديل الطلب {order.display_number}', 'تمت إضافة عنصر إلى الطلب', order=order, order_item=item, target_station=item.prep_station, created_by=request.user))
        if item.prep_status != OrderItem.PrepStatus.NO_PREP:
            transaction.on_commit(lambda: create_notification('new_prep_item', 'عنصر جديد للتحضير', f'{item.product_name_ar_snapshot} × {item.quantity} — {order.display_number}', order=order, order_item=item, target_station=item.prep_station, created_by=request.user))
    messages.success(request, 'تمت إضافة العنصر إلى الطلب.')
    return redirect('staff_order_edit', public_code=order.public_code)


@require_staff_capability('order_edit')
def staff_order_edit_update_item(request, public_code, item_id):
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order.objects.prefetch_related('items', 'payments'), public_code=public_code)
    blocked = _redirect_if_order_edit_blocked(request, order)
    if blocked:
        return blocked
    item = get_object_or_404(OrderItem.objects.select_related('product'), pk=item_id, order=order)

    qty = _parse_positive_int(request.POST.get('quantity'), default=item.quantity)
    item_note = request.POST.get('item_note', '').strip()
    old_values = {
        'quantity': item.quantity,
        'item_note': item.item_note,
        'unit_price_syp_snapshot': item.unit_price_syp_snapshot,
        'selected_options_snapshot': item.selected_options_snapshot,
    }
    changed_fields = []
    if item.quantity != qty:
        item.quantity = qty
        changed_fields.append('quantity')
    if item.item_note != item_note:
        item.item_note = item_note
        changed_fields.append('item_note')

    if request.POST.get('edit_scope') == 'options':
        product = Product.objects.filter(pk=item.product_id).prefetch_related(_option_assignment_prefetch()).first()
        if not product:
            messages.error(request, 'لا يمكن تعديل خيارات عنصر غير مرتبط بمنتج.')
            return redirect('staff_order_edit', public_code=order.public_code)
        _attach_valid_option_assignments([product])
        selected_options_snapshot, option_delta, option_errors = _validate_product_options(request, product)
        if option_errors:
            messages.error(request, ' '.join(option_errors))
            return redirect('staff_order_edit', public_code=order.public_code)
        base_price_snapshot = item.unit_price_syp_snapshot - _snapshot_option_delta(item.selected_options_snapshot)
        item.selected_options_snapshot = selected_options_snapshot
        item.unit_price_syp_snapshot = max(base_price_snapshot + option_delta, 0)
        changed_fields.extend(['selected_options_snapshot', 'unit_price_syp_snapshot'])

    item.line_total_syp_snapshot = item.quantity * item.unit_price_syp_snapshot
    changed_fields.append('line_total_syp_snapshot')
    item.save(update_fields=sorted(set(changed_fields + ['updated_at'])))
    _log_order_edit(
        request.user,
        'order_item_updated',
        order,
        {'item_id': item.id, 'old': old_values, 'new_quantity': item.quantity, 'new_unit_price_syp_snapshot': item.unit_price_syp_snapshot},
    )
    create_notification('order_edited', f'تم تعديل الطلب {order.display_number}', 'تم تعديل كمية/ملاحظات/خيارات عنصر', order=order, order_item=item, target_station=item.prep_station, created_by=request.user)
    messages.success(request, 'تم تحديث العنصر.')
    return redirect('staff_order_edit', public_code=order.public_code)


@require_staff_capability('order_edit')
def staff_order_edit_remove_item(request, public_code, item_id):
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order.objects.prefetch_related('items', 'payments'), public_code=public_code)
    blocked = _redirect_if_order_edit_blocked(request, order)
    if blocked:
        return blocked
    item = get_object_or_404(OrderItem, pk=item_id, order=order)
    snapshot = {
        'item_id': item.id,
        'product_id': item.product_id,
        'quantity': item.quantity,
        'product_name_ar_snapshot': item.product_name_ar_snapshot,
        'unit_price_syp_snapshot': item.unit_price_syp_snapshot,
        'selected_options_snapshot': item.selected_options_snapshot,
        'item_note': item.item_note,
    }
    with transaction.atomic():
        item.delete()
        _log_order_edit(request.user, 'order_item_removed', order, snapshot)
        transaction.on_commit(lambda: create_notification('prep_item_cancelled', 'تم إلغاء عنصر', snapshot.get('product_name_ar_snapshot',''), order=order, target_station=getattr(item, 'prep_station', None), created_by=request.user))
        transaction.on_commit(lambda: create_notification('order_edited', f'تم تعديل الطلب {order.display_number}', 'تم حذف عنصر من الطلب', order=order, created_by=request.user))
    messages.success(request, 'تم حذف العنصر من الطلب.')
    return redirect('staff_order_edit', public_code=order.public_code)


@require_staff_capability('delivery_management')
def staff_delivery(request):
    orders = (
        Order.objects.filter(fulfillment_mode=Order.FulfillmentMode.DELIVERY)
        .select_related('table')
        .prefetch_related('items', 'payments')
        .order_by('-created_at')
    )
    rows = []
    for order in orders[:200]:
        total, paid, remaining, payment_label = _order_financials(order)
        rows.append({'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label})
    return render(request, 'staff/delivery.html', {'rows': rows, 'cancellation_reasons': CancellationReason.choices, 'page_setting': get_page_setting('staff_delivery', 'طلبات التوصيل', 'Delivery')})


@require_staff_capability('cashier')
def staff_cashier(request):
    query = request.GET.get('q', '').strip()
    orders = Order.objects.select_related('table', 'table__room').prefetch_related('items', 'payments').order_by('-created_at')
    if query:
        normalized = query.strip().lstrip('#')
        if normalized.isdigit():
            orders = orders.filter(pk=int(normalized))
        else:
            try:
                orders = orders.filter(public_code=uuid.UUID(query))
            except ValueError:
                orders = orders.none()
    rows = []
    for order in orders[:100]:
        total, paid, remaining, payment_label = _order_financials(order)
        rows.append({'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label})
    return render(request, 'staff/cashier.html', {'rows': rows, 'query': query, 'page_setting': get_page_setting('staff_cashier', 'الكاشير', 'Cashier')})


@require_staff_capability('cashier')
def staff_cashier_order(request, public_code):
    order = get_object_or_404(Order.objects.select_related('table', 'table__room').prefetch_related('items', 'payments', 'discounts'), public_code=public_code)
    total, paid, remaining, payment_label = _order_financials(order)
    methods = Payment.Method.choices
    return render(request, 'staff/cashier_order.html', {'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label, 'methods': methods, 'discount_types': OrderDiscount.DiscountType.choices, 'can_manage_discounts': _can_approve_partial_payment(request.user), 'payment_amount_default': remaining, 'qr_url': reverse('order_qr', kwargs={'public_code': order.public_code}), 'currency_component': CurrencyEntryFormService(request, operation='payment').context})


@require_staff_capability('cashier')
def staff_cashier_pay(request, public_code):
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order, public_code=public_code)
    method = request.POST.get('method', Payment.Method.CASH)
    valid_methods = {m[0] for m in Payment.Method.choices}
    if method not in valid_methods:
        method = Payment.Method.CASH
    _total, _paid, remaining, _payment_label = _order_financials(
        Order.objects.prefetch_related('items', 'payments').get(pk=order.pk)
    )
    currency_service = CurrencyEntryFormService(request, operation='payment')
    try:
        currency_entry = currency_service.clean(request.POST.get('amount_syp'))
        amount_val = currency_entry.base_amount
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
        return redirect('staff_cashier_order', public_code=order.public_code)
    if amount_val <= 0:
        messages.error(request, 'المبلغ يجب أن يكون أكبر من صفر.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    if amount_val > remaining:
        messages.error(request, 'المبلغ لا يجوز أن يتجاوز المتبقي.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    if amount_val < remaining:
        approving_manager = request.user if _can_approve_partial_payment(request.user) else None
        if approving_manager is None:
            manager_username = request.POST.get('manager_username', '').strip()
            manager_password = request.POST.get('manager_password', '')
            manager = authenticate(request, username=manager_username, password=manager_password)
            if manager and _can_approve_partial_payment(manager):
                approving_manager = manager
        if approving_manager is None:
            create_notification('manager_approval_needed', 'مطلوب موافقة المدير', f'دفع جزئي يحتاج موافقة — {order.display_number}', order=order, target_role='admin', created_by=request.user)
            create_notification('partial_payment_requested', 'دفع جزئي يحتاج موافقة', f'{amount_val} ل.س — {order.display_number}', order=order, target_role='admin', created_by=request.user)
            messages.error(request, 'الدفع الجزئي يحتاج موافقة المدير أو صاحب المحل.')
            return redirect('staff_cashier_order', public_code=order.public_code)
        ActivityLog.objects.create(
            actor=request.user,
            action='partial_payment_approved',
            details={
                'order_display_number': order.display_number,
                'order_public_code': str(order.public_code),
                'payment_amount': amount_val,
                'remaining_before_payment': remaining,
                'approving_manager_username': approving_manager.username,
                'cashier_username': request.user.username,
            },
        )
    if not method:
        messages.error(request, 'طريقة الدفع مطلوبة.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    import hashlib
    digest=hashlib.sha256(repr(sorted((key, request.POST.getlist(key)) for key in request.POST)).encode()).hexdigest()[:24]
    posting_key=request.headers.get('Idempotency-Key') or request.POST.get('idempotency_key') or f'cashier:{order.pk}:{request.user.pk}:{digest}'
    duplicate = PostingCommand.objects.filter(key=posting_key).exists()
    try:
        payment = collect_order_payment(order, PostingContext(actor=request.user,approver=locals().get('approving_manager'),business_date=timezone.localdate(),idempotency_key=posting_key,channel='cashier',request_metadata={'path':request.path}), amount_val, method, request.POST.get('notes', '').strip())
        currency_service.snapshot(payment, currency_entry, 'amount_syp')
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('staff_cashier_order', public_code=order.public_code)
    if duplicate:
        messages.warning(request, 'طلب دفع مكرر: لم يتم إنشاء دفعة أو حركة صندوق إضافية.')
    if order.remaining_syp > 0:
        create_notification('payment_pending', f'الدفع بانتظار الكاشير {order.display_number}', f'المتبقي {order.remaining_syp} ل.س', order=order, target_role='cashier', created_by=request.user)
    try:
        ActivityLog.objects.create(actor=request.user, action='payment_added', details={'order_public_code': str(order.public_code), 'payment_id': payment.id, 'amount_syp': amount_val, 'method': method})
    except Exception:
        pass
    return redirect('staff_cashier_order', public_code=order.public_code)


@require_staff_capability('cashier')
def staff_cashier_discount(request, public_code):
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order.objects.prefetch_related('items', 'payments', 'discounts'), public_code=public_code)
    if not _can_approve_partial_payment(request.user):
        messages.error(request, 'إضافة الخصم تحتاج موافقة المدير.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'سبب الخصم مطلوب.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    discount_type = request.POST.get('discount_type', OrderDiscount.DiscountType.FIXED)
    if discount_type not in set(OrderDiscount.DiscountType.values):
        discount_type = OrderDiscount.DiscountType.FIXED
    amount = _parse_nonnegative_int(request.POST.get('amount_syp'), default=0, maximum=10_000_000)
    if amount <= 0:
        messages.error(request, 'قيمة الخصم يجب أن تكون أكبر من صفر.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    discount = OrderDiscount(order=order, discount_type=discount_type, amount_syp=amount, reason=reason, notes=request.POST.get('notes', '').strip(), created_by=request.user, approved_by=request.user)
    try:
        discount.full_clean()
    except ValidationError as exc:
        messages.error(request, ' '.join(sum(exc.message_dict.values(), [])) if hasattr(exc, 'message_dict') else 'الخصم غير صالح.')
        return redirect('staff_cashier_order', public_code=order.public_code)
    discount.save()
    create_notification('discount_added', f'تمت إضافة خصم {order.display_number}', f'{amount} ل.س', order=order, target_role='cashier', created_by=request.user)
    try:
        ActivityLog.objects.create(actor=request.user, action='discount_added', details={'order_public_code': str(order.public_code), 'discount_id': discount.id, 'amount_syp': amount, 'type': discount_type})
    except Exception:
        pass
    messages.success(request, 'تمت إضافة الخصم.')
    return redirect('staff_cashier_order', public_code=order.public_code)



def _parse_range_dates(request):
    today = datetime.now(DAMASCUS_TZ).date()
    date_from = _parse_report_date(request.GET.get('date_from', '').strip() or today.isoformat())
    date_to = _parse_report_date(request.GET.get('date_to', '').strip() or date_from.isoformat())
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def _product_margin_context(request):
    date_from, date_to = _parse_range_dates(request)
    start_utc, _ = _day_range_utc(date_from)
    _, end_utc = _day_range_utc(date_to)
    product_type = request.GET.get('product_type', '').strip()
    category_id = request.GET.get('category', '').strip()
    section_id = request.GET.get('section', '').strip()
    prep_station_id = request.GET.get('prep_station', '').strip()
    only_active = request.GET.get('only_active', '1') == '1'
    only_sold = request.GET.get('only_sold', '') == '1'

    products_qs = Product.objects.select_related('category', 'prep_station_ref').prefetch_related('menu_sections').order_by('category__name_ar', 'name_ar')
    if only_active:
        products_qs = products_qs.filter(is_available=True)
    if product_type:
        products_qs = products_qs.filter(product_type=product_type)
    if category_id.isdigit():
        products_qs = products_qs.filter(category_id=int(category_id))
    if section_id.isdigit():
        products_qs = products_qs.filter(menu_sections__id=int(section_id))
    if prep_station_id.isdigit():
        products_qs = products_qs.filter(prep_station_ref_id=int(prep_station_id))

    items = (OrderItem.objects.select_related('product', 'product__category', 'product__prep_station_ref')
        .prefetch_related('product__menu_sections')
        .filter(order__created_at__gte=start_utc, order__created_at__lt=end_utc)
        .exclude(order__status=Order.Status.CANCELLED))
    if product_type:
        items = items.filter(product__product_type=product_type)
    if category_id.isdigit():
        items = items.filter(product__category_id=int(category_id))
    if section_id.isdigit():
        items = items.filter(product__menu_sections__id=int(section_id))
    if prep_station_id.isdigit():
        items = items.filter(product__prep_station_ref_id=int(prep_station_id))

    stats = {}
    for item in items:
        product = item.product
        row = stats.setdefault(product.id, {'product': product, 'units_sold': 0, 'gross_sales_syp': 0, 'estimated_cost_syp': 0, 'known_cost': True})
        row['units_sold'] += item.quantity
        margin = item_margin(item, allow_current_product_cost=True)
        row['gross_sales_syp'] += margin['revenue_syp']
        if margin['estimated_cost_syp'] is None:
            row['known_cost'] = False
        else:
            row['estimated_cost_syp'] += margin['estimated_cost_syp']
    discount_total = sum(o.discount_syp for o in Order.objects.filter(created_at__gte=start_utc, created_at__lt=end_utc).prefetch_related('discounts', 'items'))
    cancelled_items = OrderItem.objects.filter(order__created_at__gte=start_utc, order__created_at__lt=end_utc, order__status=Order.Status.CANCELLED)
    cancelled_by_product = {}
    for item in cancelled_items:
        data = cancelled_by_product.setdefault(item.product_id, {'units': 0, 'value': 0})
        data['units'] += item.quantity
        data['value'] += item.line_total_syp_snapshot if item.line_total_syp_snapshot is not None else item.quantity * item.unit_price_syp_snapshot

    rows = []
    for product in products_qs.distinct():
        stat = stats.get(product.id, {'product': product, 'units_sold': 0, 'gross_sales_syp': 0, 'estimated_cost_syp': 0, 'known_cost': product.estimated_unit_cost_syp is not None})
        if only_sold and stat['units_sold'] <= 0:
            continue
        est_cost = stat['estimated_cost_syp'] if stat['known_cost'] else None
        est_margin, est_percent = product_margin_from_values(stat['gross_sales_syp'], est_cost)
        cancelled = cancelled_by_product.get(product.id, {'units': 0, 'value': 0})
        rows.append({**stat, 'net_sales_syp': stat['gross_sales_syp'], 'estimated_cost_display': est_cost, 'estimated_margin_syp': est_margin, 'estimated_margin_percent': est_percent, 'missing_cost': not stat['known_cost'], 'cancelled_units': cancelled['units'], 'cancelled_value_syp': cancelled['value'], 'avg_selling_price': int(stat['gross_sales_syp'] / stat['units_sold']) if stat['units_sold'] else 0})

    section_rows = {}
    for row in rows:
        sections = list(row['product'].menu_sections.all()) or [row['product'].category]
        for section in sections:
            key = f'{section.__class__.__name__}:{section.id}'
            data = section_rows.setdefault(key, {'name': getattr(section, 'name_ar', str(section)), 'units_sold': 0, 'gross_sales_syp': 0, 'estimated_cost_syp': 0, 'known_cost': True, 'product_count': 0})
            data['units_sold'] += row['units_sold']; data['gross_sales_syp'] += row['gross_sales_syp']; data['product_count'] += 1
            if row['missing_cost']:
                data['known_cost'] = False
            else:
                data['estimated_cost_syp'] += row['estimated_cost_display'] or 0
    for data in section_rows.values():
        cost = data['estimated_cost_syp'] if data['known_cost'] else None
        data['estimated_margin_syp'], data['estimated_margin_percent'] = product_margin_from_values(data['gross_sales_syp'], cost)
        data['missing_cost'] = not data['known_cost']

    sold_rows = [r for r in rows if r['units_sold'] > 0]
    cards = {
        'best_quantity': sorted(sold_rows, key=lambda r: r['units_sold'], reverse=True)[:10],
        'best_revenue': sorted(sold_rows, key=lambda r: r['gross_sales_syp'], reverse=True)[:10],
        'highest_margin': sorted([r for r in sold_rows if not r['missing_cost']], key=lambda r: r['estimated_margin_syp'], reverse=True)[:10],
        'lowest_margin': sorted([r for r in sold_rows if not r['missing_cost']], key=lambda r: r['estimated_margin_syp'])[:10],
        'missing_cost': [r for r in sold_rows if r['missing_cost']][:25],
    }
    categories = Category.objects.order_by('name_ar')
    sections = MenuSection.objects.order_by('name_ar')
    prep_stations = PrepStation.objects.order_by('name_ar')
    return {'date_from': date_from, 'date_to': date_to, 'rows': rows, 'section_rows': sorted(section_rows.values(), key=lambda r: r['gross_sales_syp'], reverse=True), 'cards': cards, 'discount_total': discount_total, 'categories': categories, 'sections': sections, 'prep_stations': prep_stations, 'product_types': Product.ProductType.choices, 'filters': request.GET}

@require_staff_capability('reports')
def staff_reports_home(request):
    today = datetime.now(DAMASCUS_TZ).date()
    _rows, sums = _build_day_report(today)
    return render(request, 'staff/reports_home.html', {'report_date': today, 'sums': sums})


@require_staff_capability('reports')
def staff_reports_day(request):
    report_date = _parse_report_date(request.GET.get('date', '').strip())
    rows, sums = _build_day_report(report_date)
    return render(request, 'staff/reports_day.html', {'report_date': report_date, 'rows': rows, 'sums': sums})


@require_staff_capability('reports')
def staff_reports_day_csv(request):
    report_date = _parse_report_date(request.GET.get('date', '').strip())
    rows, _sums = _build_day_report(report_date)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="report-{report_date.isoformat()}.csv"'
    response.write('display_number,created_at,fulfillment_mode,delivery_status,delivery_fee,delivery_phone,delivery_address,location,status,subtotal,total,paid,remaining,payment_methods,session_type,billing_mode,raw_minutes,billable_minutes,calculated_total,linked_order,payment_status\n')
    for row in rows:
        order = row['order']
        methods_txt = '; '.join(f'{k}:{v}' for k, v in row['methods'].items())
        table_name = order.location_label
        session = order.internet_sessions.first()
        session_cols = ''
        if session:
            session_cols = f'{session.get_session_type_display()},{session.get_billing_mode_display()},{session.effective_duration_minutes or 0},{session.billable_minutes or 0},{session.calculated_total_syp},{order.display_number},{session.get_status_display()}'
        response.write(f'{order.display_number},{order.created_at.isoformat()},{order.get_fulfillment_label()},{order.get_delivery_status_display()},{order.delivery_fee_syp},"{order.delivery_phone}","{order.delivery_address}",{table_name},{order.get_status_display()},{order.subtotal_syp},{row["total"]},{row["paid"]},{row["remaining"]},"{methods_txt}",{session_cols}\n')
    return response



@require_staff_capability('reports')
def staff_product_margin_report(request):
    return render(request, 'staff/reports_products.html', _product_margin_context(request))


@require_staff_capability('reports')
def staff_product_margin_csv(request):
    context = _product_margin_context(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="product-margins-{context["date_from"].isoformat()}-{context["date_to"].isoformat()}.csv"'
    response.write('product_id,product_name,product_type,section,units_sold,gross_sales,net_sales,estimated_cost,estimated_margin,margin_percent,missing_cost\n')
    for row in context['rows']:
        product = row['product']
        section = product.menu_sections.first() or product.category
        response.write(f'{product.id},"{product.name_ar}",{product.product_type},"{getattr(section, "name_ar", "")}",{row["units_sold"]},{row["gross_sales_syp"]},{row["net_sales_syp"]},{row["estimated_cost_display"] if row["estimated_cost_display"] is not None else ""},{row["estimated_margin_syp"] if row["estimated_margin_syp"] is not None else ""},{row["estimated_margin_percent"] if row["estimated_margin_percent"] is not None else ""},{"yes" if row["missing_cost"] else "no"}\n')
    return response

@require_staff_capability('reports')
def staff_close_day(request):
    # This historical endpoint is intentionally read/write incapable. Account
    # period closes are managed exclusively by core.services.posting.closing.
    messages.info(request, 'تم نقل إغلاق اليوم إلى إغلاقات الحسابات.')
    return redirect('staff_daily_closes')


def _get_member_or_404(member_id):
    try:
        parsed_uuid = uuid.UUID(str(member_id))
        return get_object_or_404(Member, public_code=parsed_uuid)
    except ValueError:
        return get_object_or_404(Member, pk=member_id)


def _active_subscription_for_member(member):
    return (
        member.subscriptions.filter(status='active')
        .order_by('-starts_at', '-created_at')
        .first()
    )




def _parse_int_or_error(raw_value, field_label, errors, *, required=False, default=None, min_value=None, max_value=None):
    value_raw = (raw_value or '').strip()
    if not value_raw:
        if required:
            errors[field_label] = f'{field_label} مطلوب.'
        return default
    try:
        value = int(value_raw)
    except (TypeError, ValueError):
        errors[field_label] = f'{field_label} يجب أن يكون رقماً صحيحاً.'
        return default
    if min_value is not None and value < min_value:
        errors[field_label] = f'{field_label} يجب أن يكون أكبر أو يساوي {min_value}.'
        return default
    if max_value is not None and value > max_value:
        errors[field_label] = f'{field_label} يجب أن يكون أصغر أو يساوي {max_value}.'
        return default
    return value


def _parse_local_dt_or_error(raw_value, field_label, errors, *, required=False, default=None):
    value_raw = (raw_value or '').strip()
    if not value_raw:
        if required:
            errors[field_label] = f'{field_label} مطلوب.'
        return default
    try:
        value = datetime.fromisoformat(value_raw)
    except ValueError:
        errors[field_label] = f'{field_label} بتنسيق وقت/تاريخ غير صحيح.'
        return default
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value
def _best_plan_benefits(plan):
    rules = plan.benefit_rules.filter(is_active=True).order_by('-priority', '-created_at')
    minutes = 0
    credit = 0
    for rule in rules:
        if rule.included_minutes and rule.included_minutes > minutes:
            minutes = rule.included_minutes
        if rule.monthly_credit_syp and rule.monthly_credit_syp > credit:
            credit = rule.monthly_credit_syp
    return minutes, credit


@login_required
def staff_members(request):
    _assert_staff_capability(request.user, 'members/internet')
    query = request.GET.get('q', '').strip()
    members = Member.objects.select_related('default_plan').prefetch_related('subscriptions').order_by('-created_at')
    if query:
        members = members.filter(Q(name_ar__icontains=query) | Q(phone__icontains=query) | Q(default_plan__code__icontains=query) | Q(subscriptions__plan__code__icontains=query)).distinct()
    rows = []
    for member in members[:200]:
        active_subscription = _active_subscription_for_member(member)
        rows.append({'member': member, 'active_subscription': active_subscription})
    return render(request, 'staff/members.html', {'rows': rows, 'query': query})


@login_required
def staff_member_new(request):
    _assert_staff_capability(request.user, 'members/internet')
    plans = MembershipPlan.objects.filter(is_active=True).order_by('name_ar')
    if request.method == 'POST':
        name_ar = request.POST.get('name_ar', '').strip()
        errors = {}
        phone = _validate_phone_input(request.POST.get('phone', ''), 'رقم الهاتف', errors, required=True)
        if not name_ar or not phone:
            return render(request, 'staff/member_form.html', {'plans': plans, 'error': 'الاسم ورقم الهاتف مطلوبان.'})
        if errors:
            return render(request, 'staff/member_form.html', {'plans': plans, 'errors': errors, 'form_values': request.POST})
        member = Member(name_ar=name_ar, phone=phone)
        default_plan_id = request.POST.get('default_plan')
        if default_plan_id:
            member.default_plan = MembershipPlan.objects.filter(pk=default_plan_id, is_active=True).first()
        if hasattr(Member, 'notes'):
            member.notes = request.POST.get('notes', '').strip()
        member.save()
        return redirect('staff_member_detail', member_id=member.public_code)
    return render(request, 'staff/member_form.html', {'plans': plans})


@login_required
def staff_member_detail(request, member_id):
    _assert_staff_capability(request.user, 'members/internet')
    member = _get_member_or_404(member_id)
    subscriptions = member.subscriptions.select_related('plan').order_by('-created_at')
    ledger = member.credit_ledger.select_related('subscription', 'created_by').order_by('-created_at')[:100]
    sessions = member.internet_sessions.select_related('package').order_by('-start_time')[:50]
    active_subscription = get_active_member_context(member)
    devices = member.device_tokens.filter(revoked_at__isnull=True).order_by('-last_used_at', '-created_at')
    activation_url = request.session.pop('member_activation_url', None)
    recent_orders = member.orders.prefetch_related('discounts').order_by('-created_at')[:20]
    return render(request, 'staff/member_detail.html', {'member': member, 'subscriptions': subscriptions, 'ledger': ledger, 'sessions': sessions, 'active_subscription': active_subscription, 'devices': devices, 'activation_url': activation_url, 'recent_orders': recent_orders})


@login_required
def staff_member_subscribe(request, member_id):
    _assert_staff_capability(request.user, 'members/internet')
    member = _get_member_or_404(member_id)
    if request.method != 'POST':
        plans = MembershipPlan.objects.filter(is_active=True).order_by('name_ar')
        return render(request, 'staff/member_subscribe.html', {'member': member, 'plans': plans, 'now': timezone.now()})
    plans = MembershipPlan.objects.filter(is_active=True).order_by('name_ar')
    plan = get_object_or_404(MembershipPlan, pk=request.POST.get('plan'), is_active=True)
    errors = {}
    starts_at = _parse_local_dt_or_error(request.POST.get('starts_at', ''), 'وقت البداية', errors, default=timezone.now())
    ends_at = _parse_local_dt_or_error(request.POST.get('ends_at', ''), 'وقت النهاية', errors, default=None)
    default_minutes, default_credit = _best_plan_benefits(plan)
    minutes_val = _parse_int_or_error(request.POST.get('remaining_internet_minutes', ''), 'الدقائق المتبقية', errors, default=default_minutes, min_value=0)
    credit_val = _parse_int_or_error(request.POST.get('remaining_credit_syp', ''), 'الرصيد المتبقي', errors, default=default_credit, min_value=0)
    if starts_at and ends_at and ends_at < starts_at:
        errors['ends_at'] = 'وقت النهاية يجب أن يكون بعد أو يساوي وقت البداية.'
    if errors:
        return render(request, 'staff/member_subscribe.html', {'member': member, 'plans': plans, 'now': timezone.now(), 'errors': errors, 'form_values': request.POST})
    with transaction.atomic():
        sub = MembershipSubscription.objects.create(member=member, plan=plan, starts_at=starts_at, ends_at=ends_at, remaining_internet_minutes=minutes_val, remaining_credit_syp=credit_val, notes=request.POST.get('notes', '').strip(), status='active')
        from members.internet_benefits import provision_subscription_internet
        provision_subscription_internet(sub)
        if minutes_val > 0:
            MemberCreditLedger.objects.create(member=member, subscription=sub, change_type='add_minutes', minutes_delta=minutes_val, notes='إضافة دقائق تلقائية من الاشتراك', created_by=request.user)
        if credit_val > 0:
            MemberCreditLedger.objects.create(member=member, subscription=sub, change_type='add_credit', credit_delta_syp=credit_val, notes='إضافة رصيد تلقائي من الاشتراك', created_by=request.user)
    return redirect('staff_member_detail', member_id=member.public_code)


@login_required
def staff_internet(request):
    _assert_staff_capability(request.user, 'members/internet')
    settings_obj = get_system_settings()
    now = timezone.now()
    active_sessions = InternetSession.objects.select_related('member', 'package', 'linked_order').filter(status=InternetSession.Status.ACTIVE).order_by('-started_at', '-start_time')
    recent_sessions = InternetSession.objects.select_related('member', 'package', 'linked_order', 'linked_payment').exclude(status=InternetSession.Status.ACTIVE).order_by('-updated_at')[:50]
    packages = InternetPackage.objects.order_by('name_ar')
    members = Member.objects.order_by('-created_at')[:200]
    active_rows = []
    entitlement_base = InternetEntitlement.objects.select_related('member', 'package', 'payment', 'order').prefetch_related('devices')
    active_entitlements = effectively_active_entitlements(entitlement_base, at=now).order_by('valid_until')
    recent_entitlements = entitlement_base.order_by('-created_at')[:50]
    entitlement_rows = []
    for entitlement in recent_entitlements:
        if entitlement.access_mode == InternetPackage.AccessMode.MEMBERSHIP_CREDIT:
            commercial_label = 'رصيد عضوية'
        elif entitlement.gross_amount_syp == 0 or (
                entitlement.payment_id and entitlement.payment.method == Payment.Method.FREE):
            commercial_label = 'مجاني'
        elif (entitlement.payment_id
              and entitlement.payment.method == Payment.Method.MEMBER_DISCOUNT):
            commercial_label = 'رصيد عضوية'
        elif (entitlement.payment_id
              and entitlement.payment.method in {Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER}
              and entitlement.payment.is_active and not entitlement.payment.is_reversed):
            commercial_label = 'مدفوع'
        else:
            commercial_label = 'غير مدفوع'

        effective_status = entitlement.effective_status(now)
        entitlement_label = dict(InternetEntitlement.Status.choices)[effective_status]
        if entitlement.network_backend == 'manual':
            backend_label = 'يدوي (بدون راوتر)'
            if entitlement.network_status == InternetEntitlement.NetworkStatus.DISCONNECTED:
                network_label = 'مفصول'
            elif entitlement.network_status == InternetEntitlement.NetworkStatus.PROVISION_ERROR:
                network_label = 'خطأ'
            else:
                network_label = 'تجهيز يدوي — أو بانتظار الربط بالشبكة'
        else:
            backend_label = entitlement.network_backend
            network_label = {
                InternetEntitlement.NetworkStatus.NOT_PROVISIONED: 'بانتظار التجهيز',
                InternetEntitlement.NetworkStatus.PROVISIONED: 'مجهز على الشبكة',
                InternetEntitlement.NetworkStatus.DISCONNECTED: 'مفصول',
                InternetEntitlement.NetworkStatus.PROVISION_ERROR: 'خطأ',
            }[entitlement.network_status]
        entitlement_rows.append({
            'entitlement': entitlement, 'commercial_label': commercial_label,
            'entitlement_label': entitlement_label, 'backend_label': backend_label,
            'network_label': network_label,
            'available_minutes': get_effective_network_allowance(entitlement, now),
            'used_today': daily_minutes_used(entitlement),
            'remaining_today': daily_minutes_remaining(entitlement, now),
        })
    today = timezone.localdate()
    expiring_today = active_entitlements.filter(valid_until__date=today).count()
    for session in active_sessions:
        duration = calculate_session_duration_minutes(session.effective_started_at, now)
        estimated_total = 0 if session.billing_mode in {InternetSession.BillingMode.FREE, InternetSession.BillingMode.PREPAID} else calculate_metered_session_total(
            duration, session.rate_per_hour_syp, session.minimum_minutes, session.free_grace_minutes, session.daily_cap_syp, session.rounding_increment_minutes, session.minimum_charge_syp
        )
        active_rows.append({'session': session, 'elapsed_minutes': duration, 'estimated_total': estimated_total})
    return render(request, 'staff/internet.html', {
        'active_sessions': active_sessions,
        'active_entitlements': active_entitlements,
        'recent_entitlements': recent_entitlements,
        'entitlement_rows': entitlement_rows,
        'internet_metrics': {
            'active_sessions': active_sessions.count(), 'active_passes': active_entitlements.count(),
            'expiring_today': expiring_today,
            # An unpaid commercial sale is an order with no active collected payment.
            'unpaid': active_entitlements.filter(
                order__isnull=False, gross_amount_syp__gt=0,
                payment__isnull=True,
            ).count(),
            'provision_errors': active_entitlements.filter(network_status=InternetEntitlement.NetworkStatus.PROVISION_ERROR).count(),
        },
        'active_rows': active_rows,
        'recent_sessions': recent_sessions,
        'packages': packages,
        'members': members,
        'now': now,
        'settings_obj': settings_obj,
        'billing_modes': InternetSession.BillingMode.choices,
        'can_cancel_sessions': can_override_session_total(request.user),
        'can_override_total': can_override_session_total(request.user),
        'form_values': {},
        'sale_idempotency_key': str(uuid.uuid4()),
    })


@login_required
def staff_internet_sale(request):
    _assert_staff_capability(request.user, 'members/internet')
    if request.method != 'POST':
        return redirect('staff_internet')
    package = get_object_or_404(InternetPackage, pk=request.POST.get('package'), is_active=True, visible_to_staff=True)
    member = get_object_or_404(Member, pk=request.POST.get('member')) if request.POST.get('customer_kind') == 'member' else None
    key = request.POST.get('idempotency_key', '').strip()
    if not key:
        raise ValidationError('معرّف العملية مطلوب.')
    try:
        entitlement = create_commercial_sale(package, payment_method=request.POST.get('payment_method', Payment.Method.UNPAID),
            member=member, guest_name=request.POST.get('guest_name', '').strip(),
            guest_phone=request.POST.get('guest_phone', '').strip(),
            subscription=_active_subscription_for_member(member) if member else None,
            actor=request.user, idempotency_key=key)
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('staff_internet')
    messages.success(request, f'تم إنشاء الوصول. القسيمة الثابتة: {entitlement.access_code}')
    return redirect('staff_internet')


@login_required
def staff_internet_start(request):
    _assert_staff_capability(request.user, 'members/internet')
    if request.method != 'POST':
        return redirect('staff_internet')
    settings_obj = get_system_settings()
    errors = {}
    session_kind = request.POST.get('session_kind', 'guest')
    member = None
    if session_kind == 'member':
        if not settings_obj.allow_member_internet_sessions:
            errors['member'] = 'جلسات الأعضاء غير مفعلة حالياً.'
        member_id = request.POST.get('member')
        if member_id:
            member = Member.objects.filter(pk=member_id).first()
        if member is None:
            errors['member'] = 'اختر عضواً صحيحاً.'
    else:
        if not settings_obj.allow_guest_internet_sessions:
            errors['guest'] = 'جلسات الزوار غير مفعلة حالياً.'
    guest_phone = _validate_phone_input(request.POST.get('guest_phone') or request.POST.get('customer_phone', ''), 'هاتف الزائر', errors, required=settings_obj.require_phone_for_guest_session and session_kind != 'member')
    started_at = _parse_local_dt_or_error(request.POST.get('started_at') or request.POST.get('start_time', ''), 'وقت البدء', errors, default=timezone.now())
    billing_mode = request.POST.get('billing_mode') or settings_obj.default_internet_billing_mode
    if billing_mode not in {choice[0] for choice in InternetSession.BillingMode.choices}:
        billing_mode = InternetSession.BillingMode.OPEN_METERED
    if billing_mode == InternetSession.BillingMode.OPEN_METERED and not settings_obj.internet_metered_enabled:
        errors['billing_mode'] = 'المحاسبة حسب الوقت غير مفعلة حالياً.'
    rate = _parse_int_or_error(request.POST.get('rate_per_hour_syp', ''), 'سعر الساعة', errors, default=settings_obj.default_rate_per_hour_syp, min_value=0)
    minimum = _parse_int_or_error(request.POST.get('minimum_minutes', ''), 'الحد الأدنى للدقائق', errors, default=settings_obj.default_minimum_minutes, min_value=0)
    grace = _parse_int_or_error(request.POST.get('free_grace_minutes', ''), 'دقائق السماح', errors, default=settings_obj.default_free_grace_minutes, min_value=0)
    daily_cap = _parse_int_or_error(request.POST.get('daily_cap_syp', ''), 'السقف اليومي', errors, default=settings_obj.default_daily_cap_syp, min_value=0)
    rounding = _parse_int_or_error(request.POST.get('rounding_increment_minutes', ''), 'التقريب كل', errors, default=settings_obj.default_rounding_increment_minutes, min_value=1)
    if rounding not in {15, 30, 60}:
        errors['rounding_increment_minutes'] = 'التقريب يجب أن يكون 15 أو 30 أو 60 دقيقة.'
    minimum_charge = _parse_int_or_error(request.POST.get('minimum_charge_syp', ''), 'الحد الأدنى للدفع', errors, default=settings_obj.default_minimum_charge_syp, min_value=0)
    session_type = request.POST.get('session_type') or InternetSession.SessionType.INTERNET
    if session_type not in {choice[0] for choice in InternetSession.SessionType.choices}:
        session_type = InternetSession.SessionType.INTERNET
    package = None
    package_id = request.POST.get('package')
    if package_id:
        package = InternetPackage.objects.filter(pk=package_id).first()
    if package and billing_mode == InternetSession.BillingMode.PREPAID:
        rate = rate or package.price_syp
        minimum = minimum or package.duration_minutes
    if errors:
        active_sessions = InternetSession.objects.select_related('member', 'package').filter(status=InternetSession.Status.ACTIVE).order_by('-started_at', '-start_time')
        recent_sessions = InternetSession.objects.select_related('member', 'package').exclude(status=InternetSession.Status.ACTIVE).order_by('-updated_at')[:50]
        packages = InternetPackage.objects.order_by('name_ar')
        members = Member.objects.order_by('-created_at')[:200]
        return render(request, 'staff/internet.html', {
            'active_sessions': active_sessions, 'active_rows': [], 'recent_sessions': recent_sessions,
            'packages': packages, 'members': members, 'now': timezone.now(), 'errors': errors,
            'form_values': request.POST, 'settings_obj': settings_obj, 'billing_modes': InternetSession.BillingMode.choices,
            'can_cancel_sessions': can_override_session_total(request.user), 'can_override_total': can_override_session_total(request.user),
        })
    session = InternetSession.objects.create(
        session_type=session_type,
        member=member,
        package=package,
        guest_name=(request.POST.get('guest_name') or request.POST.get('customer_name', '')).strip(),
        guest_phone=guest_phone,
        customer_name=(request.POST.get('guest_name') or request.POST.get('customer_name', '')).strip(),
        customer_phone=guest_phone,
        billing_mode=billing_mode,
        started_at=started_at,
        start_time=started_at,
        rate_per_hour_syp=rate or 0,
        minimum_minutes=minimum or 0,
        free_grace_minutes=grace or 0,
        rounding_increment_minutes=rounding or 15,
        minimum_charge_syp=minimum_charge or 0,
        daily_cap_syp=daily_cap,
        notes=request.POST.get('notes', '').strip(),
        status=InternetSession.Status.ACTIVE,
        started_by=request.user,
    )
    try:
        create_notification('session_started', 'بدء الجلسة', str(session), target_role='cashier', created_by=request.user)
    except Exception:
        pass
    messages.success(request, f'تم بدء جلسة #{session.id}.')
    return redirect('staff_internet_session', session_id=session.id)


def _session_preview(session):
    ended_at = timezone.now()
    duration = calculate_session_duration_minutes(session.effective_started_at, ended_at)
    billable = calculate_billable_minutes(duration, session.minimum_minutes, session.free_grace_minutes, session.rounding_increment_minutes)
    calculated_total = 0 if session.billing_mode in {InternetSession.BillingMode.FREE, InternetSession.BillingMode.PREPAID} else calculate_metered_session_total(
        duration, session.rate_per_hour_syp, session.minimum_minutes, session.free_grace_minutes, session.daily_cap_syp, session.rounding_increment_minutes, session.minimum_charge_syp
    )
    subscription = _active_subscription_for_member(session.member) if session.member_id else None
    return {'ended_at': ended_at, 'duration': duration, 'billable_minutes': billable, 'calculated_total': calculated_total, 'subscription': subscription}


@login_required
def staff_internet_session(request, session_id):
    _assert_staff_capability(request.user, 'members/internet')
    session = get_object_or_404(InternetSession.objects.select_related('member', 'package', 'linked_order', 'linked_payment', 'started_by', 'ended_by'), pk=session_id)
    ledger_entries = session.member.credit_ledger.order_by('-created_at')[:20] if session.member_id else []
    preview = _session_preview(session) if session.status == InternetSession.Status.ACTIVE else None
    entitlement = session.entitlement
    return render(request, 'staff/internet_session.html', {
        'session': session,
        'preview': preview,
        'ledger_entries': ledger_entries,
        'can_cancel_sessions': can_override_session_total(request.user),
        'can_override_total': can_override_session_total(request.user),
        'settings_obj': get_system_settings(),
        'available_minutes': (get_effective_network_allowance(entitlement) if entitlement else None),
        'used_today': (daily_minutes_used(entitlement) if entitlement else None),
        'remaining_today': (daily_minutes_remaining(entitlement) if entitlement else None),
    })


@login_required
def staff_internet_end(request, session_id):
    _assert_staff_capability(request.user, 'members/internet')
    session = get_object_or_404(InternetSession.objects.select_related('member', 'package'), pk=session_id)
    if request.method != 'POST':
        return render(request, 'staff/internet_end.html', {
            'session': session,
            'preview': _session_preview(session),
            'can_override_total': can_override_session_total(request.user),
            'settings_obj': get_system_settings(),
        })
    if session.status != InternetSession.Status.ACTIVE:
        return redirect('staff_internet_session', session_id=session_id)
    manual_total = request.POST.get('manual_total_syp', '').strip()
    override_reason = request.POST.get('override_reason', '').strip()
    try:
        session = finalize_internet_session(session, request.user, manual_total=manual_total or None, override_reason=override_reason or None)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc))
        return render(request, 'staff/internet_end.html', {
            'session': session,
            'preview': _session_preview(session),
            'can_override_total': can_override_session_total(request.user),
            'settings_obj': get_system_settings(),
        })
    settings_obj = get_system_settings()
    try:
        create_notification('session_ended', 'إنهاء الجلسة', str(session), target_role='cashier', created_by=request.user)
    except Exception:
        pass
    if request.POST.get('create_order') or (settings_obj.auto_create_order_for_metered_sessions and session.payable_total_syp > 0):
        _create_order_for_internet_session(request, session, mark_paid=request.POST.get('mark_paid') == '1')
    elif request.POST.get('mark_paid') == '1':
        messages.warning(request, 'لا يمكن تسجيل دفع آمن دون طلب كاشير مرتبط؛ أنشئ طلباً أولاً أو اضبط منتج خدمة الإنترنت في الإعدادات.')
    messages.success(request, f'تم إنهاء جلسة #{session.id}.')
    return redirect('staff_internet_session', session_id=session_id)


def _create_order_for_internet_session(request, session, mark_paid=False):
    if session.linked_order_id:
        order = session.linked_order
    else:
        settings_obj = get_system_settings()
        product = settings_obj.default_workspace_product if session.session_type == InternetSession.SessionType.WORKSPACE else settings_obj.internet_service_product
        if product is None:
            messages.warning(request, 'اضبط منتج الإنترنت أو مساحة العمل الافتراضي في إعدادات النظام قبل إنشاء طلب كاشير للجلسة.')
            return None
        customer = session.member.name_ar if session.member_id else (session.display_guest_name or 'زائر')
        note = f'جلسة {session.get_session_type_display()} #{session.id}\nالوقت الفعلي: {session.effective_duration_minutes or 0} دقيقة\nالوقت المحسوب: {session.billable_minutes or 0} دقيقة\nالعميل: {customer}'
        order = Order.objects.create(service_mode=Order.ServiceMode.DINE_IN, status=Order.Status.NEW, notes=note)
        prep_station, prep_status = _prep_defaults_for_product(product)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=1,
            product_name_ar_snapshot=product.name_ar,
            product_name_en_snapshot=product.name_en,
            unit_price_syp_snapshot=session.payable_total_syp,
            selected_options_snapshot=[],
            item_note=note,
            line_total_syp_snapshot=session.payable_total_syp,
            prep_station=prep_station,
            prep_status=prep_status,
        )
        session.linked_order = order
        session.status = InternetSession.Status.BILLED if session.payable_total_syp > 0 else InternetSession.Status.ENDED
        session.save(update_fields=['linked_order', 'status', 'updated_at'])
    if mark_paid and session.payable_total_syp > 0:
        existing_paid = _paid_total(order.payments.all())
        amount = max(session.payable_total_syp - existing_paid, 0)
        if amount:
            payment = collect_order_payment(order, PostingContext(business_date=timezone.localdate(),idempotency_key=f'internet-session:{session.pk}:payment:{amount}',channel='internet'), amount, Payment.Method.CASH)
            session.linked_payment = payment
            session.status = InternetSession.Status.PAID
            session.save(update_fields=['linked_payment', 'status', 'updated_at'])
    return order


@login_required
def staff_internet_cancel(request, session_id):
    _assert_staff_capability(request.user, 'members/internet')
    if not can_override_session_total(request.user):
        raise PermissionDenied('إلغاء الجلسات يحتاج صلاحية مدير.')
    if request.method != 'POST':
        return redirect('staff_internet_session', session_id=session_id)
    session = get_object_or_404(InternetSession, pk=session_id)
    if session.status == InternetSession.Status.ACTIVE:
        reason = request.POST.get('cancellation_reason', '').strip()
        if not reason:
            messages.error(request, 'سبب الإلغاء مطلوب.')
            return redirect('staff_internet_session', session_id=session_id)
        session.status = InternetSession.Status.CANCELLED
        session.cancellation_reason = reason
        session.ended_at = timezone.now()
        session.end_time = session.ended_at
        session.ended_by = request.user
        session.save(update_fields=['status', 'cancellation_reason', 'ended_at', 'end_time', 'ended_by', 'updated_at'])
        try:
            create_notification('session_cancelled', 'إلغاء الجلسة', reason, target_role='admin', created_by=request.user)
        except Exception:
            pass
        messages.success(request, 'تم إلغاء الجلسة.')
    return redirect('staff_internet_session', session_id=session_id)


@login_required
def staff_wifi(request):
    _assert_staff_capability(request.user, 'members/internet')
    networks = WifiNetwork.objects.filter(is_active=True).order_by('name_ar')
    return render(request, 'staff/wifi.html', {'networks': networks})


@login_required
def staff_events(request):
    _assert_staff_capability(request.user, 'events')
    events = Event.objects.select_related('room').order_by('starts_at', '-created_at')
    return render(request, 'staff/events.html', {'events': events})


@login_required
def staff_event_new(request):
    _assert_staff_capability(request.user, 'events')
    rooms = Room.objects.order_by('name_ar')
    if request.method == 'POST':
        title_ar = request.POST.get('title_ar', '').strip()
        starts_at_raw = request.POST.get('starts_at', '').strip()
        if not title_ar or not starts_at_raw:
            return render(request, 'staff/event_form.html', {'rooms': rooms, 'statuses': Event.Status.choices, 'error': 'الاسم ووقت البداية مطلوبان.'})
        errors = {}
        starts_at = _parse_local_dt_or_error(starts_at_raw, 'وقت البداية', errors, required=True)
        ends_at = _parse_local_dt_or_error(request.POST.get('ends_at', ''), 'وقت النهاية', errors, default=None)
        if starts_at and ends_at and ends_at < starts_at:
            errors['ends_at'] = 'وقت النهاية يجب أن يكون بعد أو يساوي وقت البداية.'
        capacity = _parse_int_or_error(request.POST.get('capacity'), 'السعة', errors, default=0, min_value=0)
        if errors:
            return render(request, 'staff/event_form.html', {'rooms': rooms, 'statuses': Event.Status.choices, 'errors': errors, 'form_values': request.POST})
        event = Event(
            title_ar=title_ar,
            title_en=request.POST.get('title_en', '').strip(),
            description_ar=request.POST.get('description_ar', '').strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            capacity=capacity or None,
            status=request.POST.get('status') or Event.Status.DRAFT,
        )
        room_id = request.POST.get('room')
        if room_id:
            event.room = Room.objects.filter(pk=room_id).first()
        event.save()
        return redirect('staff_event_detail', event_id=event.id)
    return render(request, 'staff/event_form.html', {'rooms': rooms, 'statuses': Event.Status.choices})


@login_required
def staff_event_detail(request, event_id):
    _assert_staff_capability(request.user, 'events')
    event = get_object_or_404(Event.objects.select_related('room'), pk=event_id)
    reservations = Reservation.objects.filter(event=event).select_related('table_area').order_by('reservation_date', 'start_time')
    participations = VendorParticipation.objects.filter(event=event).select_related('vendor', 'location_area', 'event').order_by('starts_at')
    legacy_participations = VendorParticipation.objects.filter(event__isnull=True, notes__icontains=event.title_ar).select_related('vendor', 'location_area').order_by('starts_at')
    return render(request, 'staff/event_detail.html', {'event': event, 'reservations': reservations, 'participations': participations, 'legacy_participations': legacy_participations})


@login_required
def staff_reservations(request):
    _assert_staff_capability(request.user, 'reservations')
    today = timezone.localdate()
    all_rows = Reservation.objects.select_related('room', 'table_area__room', 'event__room').order_by('reservation_date', 'start_time')
    active = all_rows.exclude(status=Reservation.Status.CANCELLED)
    today_rows = active.filter(Q(reservation_type=Reservation.ReservationType.EVENT, event__starts_at__date=today) | Q(reservation_type=Reservation.ReservationType.REGULAR, reservation_date=today))
    upcoming_rows = active.exclude(status=Reservation.Status.COMPLETED).filter(Q(reservation_type=Reservation.ReservationType.EVENT, event__starts_at__date__gt=today) | Q(reservation_type=Reservation.ReservationType.REGULAR, reservation_date__gt=today))
    past_cancelled_rows = all_rows.filter(Q(status__in=[Reservation.Status.CANCELLED, Reservation.Status.COMPLETED]) | Q(reservation_type=Reservation.ReservationType.EVENT, event__starts_at__date__lt=today) | Q(reservation_type=Reservation.ReservationType.REGULAR, reservation_date__lt=today))
    return render(request, 'staff/reservations.html', {'today_rows': today_rows, 'upcoming_rows': upcoming_rows, 'past_cancelled_rows': past_cancelled_rows})


@login_required
def staff_reservation_new(request):
    _assert_staff_capability(request.user, 'reservations')
    events = Event.objects.select_related('room').order_by('starts_at')
    rooms = Room.objects.order_by('name_ar')
    selected_room = request.POST.get('room') or request.GET.get('room')
    table_areas = TableArea.objects.select_related('room').filter(room_id=selected_room).order_by('name_ar') if selected_room else TableArea.objects.none()
    context = {'events': events, 'rooms': rooms, 'table_areas': table_areas, 'types': Reservation.ReservationType.choices, 'statuses': Reservation.Status.choices}
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        errors = {}
        phone = _validate_phone_input(request.POST.get('phone', ''), 'رقم الهاتف', errors, required=True)
        reservation_type = request.POST.get('reservation_type') or Reservation.ReservationType.REGULAR
        reservation_date_raw = request.POST.get('reservation_date', '').strip() or None
        start_time_raw = request.POST.get('start_time', '').strip() or None
        if not (name and phone):
            return render(request, 'staff/reservation_form.html', {**context, 'error': 'الاسم والهاتف مطلوبان.', 'form_values': request.POST})
        party_size = _parse_int_or_error(request.POST.get('party_size') or 1, 'عدد الأشخاص', errors, default=1, min_value=1)
        if errors:
            return render(request, 'staff/reservation_form.html', {**context, 'errors': errors, 'form_values': request.POST})
        reservation = Reservation(
            name=name, phone=phone, reservation_date=reservation_date_raw, start_time=start_time_raw,
            reservation_type=reservation_type,
            party_size=party_size,
            status=request.POST.get('status') or Reservation.Status.PENDING,
            notes=request.POST.get('notes', '').strip(),
            created_by=request.user,
        )
        end_time = request.POST.get('end_time', '').strip()
        if end_time:
            reservation.end_time = end_time
        room_id = request.POST.get('room')
        if room_id:
            reservation.room = Room.objects.filter(pk=room_id).first()
        area_id = request.POST.get('table_area')
        if area_id:
            reservation.table_area = TableArea.objects.filter(pk=area_id).first()
        event_id = request.POST.get('event')
        if event_id:
            reservation.event = Event.objects.filter(pk=event_id).first()
        try:
            create_reservation(reservation)
        except ValidationError as exc:
            return render(request, 'staff/reservation_form.html', {**context, 'errors': exc.message_dict, 'form_values': request.POST})
        return redirect('staff_reservation_detail', reservation_id=reservation.id)
    initial_type = Reservation.ReservationType.EVENT if request.GET.get('event') else Reservation.ReservationType.REGULAR
    return render(request, 'staff/reservation_form.html', {**context, 'form_values': {'reservation_type': initial_type, 'event': request.GET.get('event', '')}})


@login_required
def staff_reservation_tables(request):
    """Small, permission-protected dependency endpoint for a selected room."""
    _assert_staff_capability(request.user, 'reservations')
    room_id = request.GET.get('room')
    rows = TableArea.objects.filter(room_id=room_id).order_by('name_ar')[:50] if room_id else []
    return JsonResponse({'results': [{'id': row.pk, 'text': str(row)} for row in rows], 'pagination': {'more': False}})


@login_required
def staff_reservation_detail(request, reservation_id):
    _assert_staff_capability(request.user, 'reservations')
    reservation = get_object_or_404(Reservation.objects.select_related('room', 'table_area__room', 'event__room'), pk=reservation_id)
    return render(request, 'staff/reservation_detail.html', {'reservation': reservation, 'statuses': Reservation.Status.choices})


@login_required
def staff_reservation_status(request, reservation_id):
    _assert_staff_capability(request.user, 'reservations')
    if request.method != 'POST':
        raise Http404()
    reservation = get_object_or_404(Reservation, pk=reservation_id)
    new_status = request.POST.get('status')
    try:
        if request.POST.get('action') == 'correct':
            Reservation.correct_status(reservation.pk, actor=request.user, new_status=new_status, reason=request.POST.get('reason', ''))
        else:
            Reservation.transition_status(reservation.pk, actor=request.user, new_status=new_status)
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('staff_reservation_detail', reservation_id=reservation.id)


@login_required
def staff_vendors(request):
    _assert_staff_capability(request.user, 'vendors')
    vendors = Vendor.objects.order_by('name_ar')
    return render(request, 'staff/vendors.html', {'vendors': vendors})


@login_required
def staff_vendor_new(request):
    _assert_staff_capability(request.user, 'vendors')
    if request.method == 'POST':
        name_ar = request.POST.get('name_ar', '').strip()
        if not name_ar:
            return render(request, 'staff/vendor_form.html', {'types': Vendor.VendorType.choices, 'error': 'اسم الشريك مطلوب.'})
        errors = {}
        phone = _validate_phone_input(request.POST.get('phone', ''), 'رقم الهاتف', errors, required=False)
        if errors:
            return render(request, 'staff/vendor_form.html', {'types': Vendor.VendorType.choices, 'errors': errors, 'form_values': request.POST})
        vendor = Vendor.objects.create(
            name_ar=name_ar,
            name_en=request.POST.get('name_en', '').strip(),
            vendor_type=request.POST.get('vendor_type') or Vendor.VendorType.OTHER,
            contact_person=request.POST.get('contact_person', '').strip(),
            phone=phone,
            settlement_notes=request.POST.get('settlement_notes', '').strip(),
            is_active=bool(request.POST.get('is_active')),
        )
        return redirect('staff_vendor_detail', vendor_id=vendor.id)
    return render(request, 'staff/vendor_form.html', {'types': Vendor.VendorType.choices})


@login_required
def staff_vendor_detail(request, vendor_id):
    _assert_staff_capability(request.user, 'vendors')
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    participations = vendor.participations.select_related('location_area').order_by('-starts_at')
    linked_products = Product.objects.filter(vendor=vendor).order_by('name_ar')
    return render(request, 'staff/vendor_detail.html', {'vendor': vendor, 'participations': participations, 'linked_products': linked_products})


@login_required
def staff_vendor_participation_new(request, vendor_id):
    _assert_staff_capability(request.user, 'vendors')
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    areas = TableArea.objects.order_by('name_ar')
    events = Event.objects.order_by('starts_at')
    if request.method == 'POST':
        title_ar = request.POST.get('title_ar', '').strip() or f'مشاركة {vendor.name_ar}'
        starts_at_raw = request.POST.get('starts_at', '').strip()
        if not starts_at_raw:
            return render(request, 'staff/vendor_participation_form.html', {'vendor': vendor, 'areas': areas, 'events': events, 'statuses': VendorParticipation.Status.choices, 'error': 'وقت البداية مطلوب.'})
        errors = {}
        starts_at = _parse_local_dt_or_error(starts_at_raw, 'وقت البداية', errors, required=True)
        participation = VendorParticipation(vendor=vendor, title_ar=title_ar, starts_at=starts_at, notes=request.POST.get('notes', '').strip(), status=request.POST.get('status') or VendorParticipation.Status.PLANNED)
        ends_at = _parse_local_dt_or_error(request.POST.get('ends_at', ''), 'وقت النهاية', errors, default=None)
        if starts_at and ends_at and ends_at < starts_at:
            errors['ends_at'] = 'وقت النهاية يجب أن يكون بعد أو يساوي وقت البداية.'
        if ends_at:
            participation.ends_at = ends_at
        if errors:
            return render(request, 'staff/vendor_participation_form.html', {'vendor': vendor, 'areas': areas, 'events': events, 'statuses': VendorParticipation.Status.choices, 'errors': errors, 'form_values': request.POST})
        area_id = request.POST.get('location_area')
        if area_id:
            participation.location_area = TableArea.objects.filter(pk=area_id).first()
        event_id = request.POST.get('event')
        if event_id:
            participation.event = Event.objects.filter(pk=event_id).first()
        participation.save()
        return redirect('staff_vendor_detail', vendor_id=vendor.id)
    return render(request, 'staff/vendor_participation_form.html', {'vendor': vendor, 'areas': areas, 'events': events, 'statuses': VendorParticipation.Status.choices})


@login_required
def staff_food_lab(request):
    _assert_staff_capability(request.user, 'operations')
    participations = VendorParticipation.objects.select_related('vendor', 'location_area').order_by('-starts_at')
    events = Event.objects.filter(Q(title_ar__icontains='Food Lab') | Q(description_ar__icontains='Food Lab') | Q(title_ar__icontains='مختبر') | Q(description_ar__icontains='مختبر')).order_by('-starts_at')
    return render(request, 'staff/food_lab.html', {'participations': participations, 'events': events})


def _print_order_queryset():
    return Order.objects.select_related('table', 'table__room').prefetch_related('items__product', 'items__prep_station', 'payments', 'discounts')


def _safe_activity(actor, action, details):
    try:
        ActivityLog.objects.create(actor=actor, action=action, details=details)
    except Exception:
        pass


def _receipt_context(order, request, template_kind='a4'):
    total, paid, remaining, payment_label = _order_financials(order)
    system_settings = get_system_settings()
    public_url = request.build_absolute_uri(reverse('order_public', kwargs={'public_code': order.public_code}))
    return {
        'order': order,
        'total': total,
        'paid': paid,
        'remaining': remaining,
        'payment_label': payment_label,
        'business_name': (system_settings.receipt_business_name or system_settings.public_brand_title if system_settings else 'مشاريب'),
        'receipt_footer_text': (system_settings.receipt_footer_text if system_settings else '') or 'شكراً لزيارتكم',
        'show_qr': bool(getattr(system_settings, 'receipt_show_qr', False)),
        'public_order_url': public_url,
        'qr_url': reverse('order_qr', kwargs={'public_code': order.public_code}),
        'thermal_width': getattr(system_settings, 'thermal_receipt_width_mm', 80) or 80,
        'template_kind': template_kind,
    }


@require_staff_capability('cashier')
def staff_order_receipt(request, public_code):
    order = get_object_or_404(_print_order_queryset(), public_code=public_code)
    _safe_activity(request.user, 'receipt_print_viewed', {'order_public_code': str(order.public_code), 'order_display_number': order.display_number, 'template': 'a4'})
    return render(request, 'staff/prints/order_receipt.html', _receipt_context(order, request, 'a4'))


@require_staff_capability('cashier')
def staff_order_receipt_thermal(request, public_code):
    order = get_object_or_404(_print_order_queryset(), public_code=public_code)
    _safe_activity(request.user, 'receipt_print_viewed', {'order_public_code': str(order.public_code), 'order_display_number': order.display_number, 'template': 'thermal'})
    return render(request, 'staff/prints/order_receipt_thermal.html', _receipt_context(order, request, 'thermal'))


@require_staff_capability('operations')
def staff_order_prep_ticket(request, public_code):
    order = get_object_or_404(_print_order_queryset(), public_code=public_code)
    station_code = (request.GET.get('station') or '').strip()
    items = list(order.items.all())
    if station_code:
        items = [item for item in items if item.prep_station and item.prep_station.code == station_code]
    groups = {}
    for item in items:
        station = item.prep_station
        key = station.code if station else 'general'
        if key not in groups:
            groups[key] = {'station': station, 'label': station.name_ar if station else 'عام / لا يحتاج تحضير', 'items': []}
        groups[key]['items'].append(item)
    _safe_activity(request.user, 'prep_ticket_print_viewed', {'order_public_code': str(order.public_code), 'order_display_number': order.display_number, 'station': station_code or 'all'})
    return render(request, 'staff/prints/prep_ticket.html', {'order': order, 'groups': groups.values(), 'station_code': station_code})


@require_staff_capability('delivery_management')
def staff_order_delivery_ticket(request, public_code):
    order = get_object_or_404(_print_order_queryset(), public_code=public_code)
    if not order.is_delivery:
        raise Http404()
    total, paid, remaining, payment_label = _order_financials(order)
    _safe_activity(request.user, 'delivery_ticket_print_viewed', {'order_public_code': str(order.public_code), 'order_display_number': order.display_number})
    return render(request, 'staff/prints/delivery_ticket.html', {'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label})


@require_staff_capability('reports')
def staff_close_day_print(request, close_id):
    close = get_object_or_404(DailyClose.objects.select_related('closed_by'), pk=close_id)
    _rows, sums = _build_day_report(close.business_date)
    _safe_activity(request.user, 'close_day_print_viewed', {'daily_close_id': close.id, 'business_date': close.business_date.isoformat()})
    return render(request, 'staff/prints/close_day_print.html', {'close': close, 'sums': sums})
