from django.db import transaction
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
import re

from members.models import MembershipBenefitRule, MembershipPlan, MembershipSubscription, MemberCreditLedger
from internet.models import WifiNetwork
from catalog.models import MenuSection, ProductMedia, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.settings_helpers import get_page_setting, get_system_settings
from core.models import ActivityLog, InternetPackage, InternetSession, Member, Order, OrderItem, Payment, Product, SystemSetting, TableArea
from events.models import Event
from reservations.models import Reservation
from vendors.models import Vendor, VendorParticipation


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


def _order_location_note(table, service_mode=Order.ServiceMode.DINE_IN):
    if table:
        return f'الطاولة: {table.name_ar}' + (f' — المساحة: {table.room.name_ar}' if table.room_id else '')
    if service_mode == Order.ServiceMode.TAKEAWAY:
        return 'تيك أواي'
    return 'طلب عام داخل المكان'


def _can_approve_partial_payment(user):
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or getattr(user, 'role', '') in {'admin', 'owner'}


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


def _paid_total(payments):
    return sum(payment.amount_syp for payment in payments if payment.method != Payment.Method.UNPAID)


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
        Order.objects.select_related('table')
        .prefetch_related('items', 'payments')
        .filter(created_at__gte=start_utc, created_at__lt=end_utc)
        .order_by('-created_at')
    )
    rows = []
    sums = {
        'orders_count': 0, 'order_total': 0, 'paid_total': 0, 'remaining_total': 0,
        'cash_total': 0, 'manual_transfer_total': 0, 'free_total': 0, 'discount_total': 0,
        'cancelled_count': 0, 'served_or_paid_count': 0, 'unpaid_orders_count': 0,
    }
    for order in orders:
        total, paid, remaining, payment_label = _order_financials(order)
        methods = {}
        for payment in order.payments.all():
            methods[payment.method] = methods.get(payment.method, 0) + payment.amount_syp
        if order.status == Order.Status.CANCELLED:
            sums['cancelled_count'] += 1
        if order.status == Order.Status.SERVED or remaining == 0:
            sums['served_or_paid_count'] += 1
        if remaining > 0:
            sums['unpaid_orders_count'] += 1
        sums['orders_count'] += 1
        sums['order_total'] += total
        sums['paid_total'] += paid
        sums['remaining_total'] += remaining
        sums['cash_total'] += methods.get(Payment.Method.CASH, 0)
        sums['manual_transfer_total'] += methods.get(Payment.Method.MANUAL_TRANSFER, 0)
        sums['free_total'] += methods.get(Payment.Method.FREE, 0)
        sums['discount_total'] += methods.get(Payment.Method.MEMBER_DISCOUNT, 0)
        rows.append({'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label, 'methods': methods})
    sums['avg_order_value'] = int(sums['order_total'] / sums['orders_count']) if sums['orders_count'] else 0
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


def _active_media_prefetch():
    return Prefetch(
        'media',
        queryset=ProductMedia.objects.filter(is_active=True).order_by('-is_primary', 'sort_order', 'created_at'),
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


def _section_products_for_ordering(product_filter=None, section_filter=None, include_unsectioned=False):
    if product_filter is None:
        product_filter = {}
    if section_filter is None:
        section_filter = {}
    products_qs = Product.objects.filter(**product_filter).prefetch_related(_option_assignment_prefetch(), _active_media_prefetch()).order_by('sort_order', 'name_ar')
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


def _menu_context(table=None):
    settings = get_system_settings()
    section_products = _section_products_for_ordering(
        product_filter={'is_available': True, 'visible_on_qr': True},
        section_filter={'visible_on_qr': True},
    )
    page = get_page_setting('public_menu', 'القائمة العامة', 'Public menu', 'اختر طلبك داخل المكان.', 'Choose your in-space order.')
    return {'table': table, 'section_products': section_products, 'settings': settings, 'page_setting': page}


def menu_public(request):
    if request.method == 'POST':
        return _create_order_from_menu(request, table=None)
    return render(request, 'menu/menu.html', _menu_context())


def menu_table(request, qr_token):
    table = get_object_or_404(TableArea, qr_token=qr_token)
    if request.method == 'POST':
        return _create_order_from_menu(request, table=table)
    return render(request, 'menu/menu.html', _menu_context(table=table))


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


def _create_order_from_selected_items(table, selected, note_parts, status=None, service_mode=None):
    note = '\n'.join([part for part in note_parts if part])
    if table and service_mode is None:
        service_mode = Order.ServiceMode.TABLE
    service_mode = service_mode or Order.ServiceMode.DINE_IN
    with transaction.atomic():
        order = Order.objects.create(table=table, service_mode=service_mode, status=status or Order.Status.NEW, notes=note)
        for product, qty, item_note, selected_options_snapshot, option_delta in selected:
            unit_price = max(product.price_syp + option_delta, 0)
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=qty,
                product_name_ar_snapshot=product.name_ar,
                product_name_en_snapshot=product.name_en,
                unit_price_syp_snapshot=unit_price,
                selected_options_snapshot=selected_options_snapshot,
                item_note=item_note,
                line_total_syp_snapshot=qty * unit_price,
            )
    return order


def _create_order_from_menu(request, table=None):
    products = _attach_valid_option_assignments(list(Product.objects.filter(is_available=True, visible_on_qr=True, orderable_on_qr=True).prefetch_related(_option_assignment_prefetch())))
    selected, validation_errors = _selected_order_items_from_post(request, products)

    if not selected:
        context = _menu_context(table)
        context['error'] = 'يرجى اختيار عنصر واحد على الأقل.'
        context['form_values'] = request.POST
        return render(request, 'menu/menu.html', context)

    customer_name = request.POST.get('customer_name', '').strip()
    errors = {}
    customer_phone = _validate_phone_input(request.POST.get('customer_phone', ''), 'رقم الهاتف', errors, required=False)
    if errors:
        validation_errors.append(errors['رقم الهاتف'])
    if validation_errors:
        context = _menu_context(table)
        context['error'] = ' '.join(validation_errors)
        context['form_values'] = request.POST
        return render(request, 'menu/menu.html', context)
    general_note = request.POST.get('general_note', '').strip()
    service_mode = Order.ServiceMode.TABLE if table else Order.ServiceMode.DINE_IN
    note_parts = [f'الاسم: {customer_name}' if customer_name else '', f'الهاتف: {customer_phone}' if customer_phone else '', f'المكان: {_order_location_note(table, service_mode)}', general_note]
    order = _create_order_from_selected_items(table, selected, note_parts, service_mode=service_mode)
    return redirect(reverse('order_public', kwargs={'public_code': order.public_code}))

def order_public(request, public_code):
    try:
        order = Order.objects.select_related('table').prefetch_related('items__product').get(public_code=public_code)
    except Order.DoesNotExist as exc:
        raise Http404('الطلب غير موجود') from exc
    total = _items_total(order.items.all())
    needs_confirmation = any(item.product.requires_staff_confirmation for item in order.items.all() if item.product_id)
    qr_url = reverse('order_qr', kwargs={'public_code': order.public_code})
    return render(request, 'menu/order_confirm.html', {'order': order, 'total': total, 'needs_confirmation': needs_confirmation, 'qr_url': qr_url})


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


def _assert_staff_capability(user, capability_name):
    if not user.is_authenticated:
        raise PermissionDenied("غير مصرح")
    if user.is_superuser:
        return

    allowed_roles = STAFF_CAPABILITIES.get(capability_name, set())
    if getattr(user, 'role', '') not in allowed_roles:
        ActivityLog.objects.create(
            action='staff_access_denied',
            details=f'capability={capability_name}; user={user.pk}; role={getattr(user, "role", "")}',
        )
        raise PermissionDenied("غير مصرح")


def _order_financials(order):
    total = getattr(order, 'total', None)
    if total is None:
        total = _items_total(order.items.all())
    paid = getattr(order, 'paid', None)
    if paid is None:
        paid = _paid_total(order.payments.all())
    remaining = max(total - paid, 0)
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


@login_required
def staff_home(request):
    _assert_staff_capability(request.user, 'operations')
    return render(request, 'staff/home.html')


@login_required
def staff_pos(request):
    _assert_staff_capability(request.user, 'operations')
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    section_products = _section_products_for_ordering(product_filter={'is_available': True}, include_unsectioned=True)
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
        if not selected:
            context.update({'error': 'يرجى اختيار عنصر واحد على الأقل.', 'form_values': request.POST, 'selected_table_id': table_id})
            return render(request, 'staff/pos.html', context)

        errors = {}
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = _validate_phone_input(request.POST.get('customer_phone', ''), 'رقم الهاتف', errors, required=False)
        if errors:
            validation_errors.append(errors['رقم الهاتف'])
        member_id = request.POST.get('member_id', '').strip()
        member = None
        if member_id and member_id.isdigit():
            member = Member.objects.filter(pk=int(member_id)).first()
        if member_id and not member:
            validation_errors.append('العضو المحدد غير صالح.')
        if validation_errors:
            context.update({'error': ' '.join(validation_errors), 'form_values': request.POST, 'selected_table_id': table_id})
            return render(request, 'staff/pos.html', context)

        general_note = request.POST.get('general_note', '').strip()
        service_mode = Order.ServiceMode.TABLE if table else Order.ServiceMode.DINE_IN
        table_label = _order_location_note(table, service_mode)
        note_parts = [
            'Source: staff/pos',
            f'المكان: {table_label}',
            f'الاسم: {customer_name}' if customer_name else '',
            f'الهاتف: {customer_phone}' if customer_phone else '',
            f'العضو: {member.name_ar} / {member.phone}' if member else '',
            general_note,
        ]
        order = _create_order_from_selected_items(table, selected, note_parts, status=Order.Status.NEW, service_mode=service_mode)
        ActivityLog.objects.create(
            actor=request.user,
            action='staff_pos_order_created',
            details={'order_public_code': str(order.public_code), 'table_id': table.id if table else None},
        )
        return redirect('staff_cashier_order', public_code=order.public_code)

    return render(request, 'staff/pos.html', context)


@login_required
def staff_modifiers(request):
    _assert_staff_capability(request.user, 'operations')
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
    table = get_object_or_404(TableArea, qr_token=qr_token)
    path = reverse('menu_table', kwargs={'qr_token': table.qr_token})
    return _qr_svg_response(request.build_absolute_uri(path))


def staff_menu_tools(request):
    _assert_staff_capability(request.user, 'operations')
    if request.method == 'POST':
        try:
            product_code = uuid.UUID(request.POST.get('product_code', ''))
        except (TypeError, ValueError):
            messages.error(request, 'المنتج المحدد غير صالح.')
            return redirect('staff_menu_tools')
        product = get_object_or_404(Product, public_code=product_code)
        action = request.POST.get('action')
        allowed = {'is_available', 'visible_on_qr', 'orderable_on_qr'}
        if action in allowed:
            setattr(product, action, not getattr(product, action))
            product.save(update_fields=[action, 'updated_at'])
        return redirect('staff_menu_tools')

    sections = MenuSection.objects.filter(is_active=True).prefetch_related(
        Prefetch('products', queryset=Product.objects.order_by('sort_order', 'name_ar'))
    ).order_by('sort_order', 'name_ar')
    products_in_sections = set()
    grouped = []
    for section in sections:
        products = list(section.products.all())
        products_in_sections.update(p.pk for p in products)
        grouped.append((section.name_ar, products))

    ungrouped = list(Product.objects.exclude(pk__in=products_in_sections).order_by('sort_order', 'name_ar'))
    if ungrouped:
        grouped.append(('بدون قسم في المنيو', ungrouped))

    return render(request, 'staff/menu_tools.html', {'grouped': grouped})


@login_required
def staff_orders(request):
    _assert_staff_capability(request.user, 'operations')
    statuses = [choice[0] for choice in Order.Status.choices]
    orders = (
        Order.objects.select_related('table')
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


@login_required
def staff_order_status(request, public_code):
    _assert_staff_capability(request.user, 'operations')
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order, public_code=public_code)
    new_status = request.POST.get('status')
    valid = {choice[0] for choice in Order.Status.choices}
    if new_status not in valid:
        return redirect('staff_orders')
    old_status = order.status
    if old_status != new_status:
        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])
        ActivityLog.objects.create(
            actor=request.user,
            action='order_status_changed',
            details={'order_public_code': str(order.public_code), 'old_status': old_status, 'new_status': new_status},
        )
    return redirect('staff_orders')


@login_required
def staff_order_edit(request, public_code):
    _assert_staff_capability(request.user, 'operations')
    order = get_object_or_404(
        Order.objects.select_related('table').prefetch_related('items__product', 'payments'),
        public_code=public_code,
    )
    block_reason, _total, _paid, _remaining = _order_edit_block_reason(order)
    return render(request, 'staff/order_edit.html', _order_edit_context(order, error=block_reason))


@login_required
def staff_order_edit_add_item(request, public_code):
    _assert_staff_capability(request.user, 'operations')
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
        Product.objects.filter(is_available=True).prefetch_related(_option_assignment_prefetch()),
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
        )
        _log_order_edit(request.user, 'order_item_added', order, {'item_id': item.id, 'product_id': product.id, 'quantity': qty})
    messages.success(request, 'تمت إضافة العنصر إلى الطلب.')
    return redirect('staff_order_edit', public_code=order.public_code)


@login_required
def staff_order_edit_update_item(request, public_code, item_id):
    _assert_staff_capability(request.user, 'operations')
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
    messages.success(request, 'تم تحديث العنصر.')
    return redirect('staff_order_edit', public_code=order.public_code)


@login_required
def staff_order_edit_remove_item(request, public_code, item_id):
    _assert_staff_capability(request.user, 'operations')
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
    messages.success(request, 'تم حذف العنصر من الطلب.')
    return redirect('staff_order_edit', public_code=order.public_code)


@login_required
def staff_cashier(request):
    _assert_staff_capability(request.user, 'cashier/payments')
    query = request.GET.get('q', '').strip()
    orders = Order.objects.select_related('table').prefetch_related('items', 'payments').order_by('-created_at')
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


@login_required
def staff_cashier_order(request, public_code):
    _assert_staff_capability(request.user, 'cashier/payments')
    order = get_object_or_404(Order.objects.select_related('table').prefetch_related('items', 'payments'), public_code=public_code)
    total, paid, remaining, payment_label = _order_financials(order)
    methods = Payment.Method.choices
    return render(request, 'staff/cashier_order.html', {'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label, 'methods': methods, 'payment_amount_default': remaining, 'qr_url': reverse('order_qr', kwargs={'public_code': order.public_code})})


@login_required
def staff_cashier_pay(request, public_code):
    _assert_staff_capability(request.user, 'cashier/payments')
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
    try:
        amount_val = int((request.POST.get('amount_syp') or '').strip())
    except (TypeError, ValueError):
        amount_val = 0
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
    Payment.objects.create(order=order, amount_syp=amount_val, method=method)
    return redirect('staff_cashier_order', public_code=order.public_code)


@login_required
def staff_reports_home(request):
    _assert_staff_capability(request.user, 'reporting/close-day')
    today = datetime.now(DAMASCUS_TZ).date()
    _rows, sums = _build_day_report(today)
    return render(request, 'staff/reports_home.html', {'report_date': today, 'sums': sums})


@login_required
def staff_reports_day(request):
    _assert_staff_capability(request.user, 'reporting/close-day')
    report_date = _parse_report_date(request.GET.get('date', '').strip())
    rows, sums = _build_day_report(report_date)
    return render(request, 'staff/reports_day.html', {'report_date': report_date, 'rows': rows, 'sums': sums})


@login_required
def staff_reports_day_csv(request):
    _assert_staff_capability(request.user, 'reporting/close-day')
    report_date = _parse_report_date(request.GET.get('date', '').strip())
    rows, _sums = _build_day_report(report_date)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="report-{report_date.isoformat()}.csv"'
    response.write('display_number,created_at,location,status,total,paid,remaining,payment_methods\n')
    for row in rows:
        order = row['order']
        methods_txt = '; '.join(f'{k}:{v}' for k, v in row['methods'].items())
        table_name = order.location_label
        response.write(f'{order.display_number},{order.created_at.isoformat()},{table_name},{order.get_status_display()},{row["total"]},{row["paid"]},{row["remaining"]},"{methods_txt}"\n')
    return response


@login_required
def staff_close_day(request):
    _assert_staff_capability(request.user, 'reporting/close-day')
    today = datetime.now(DAMASCUS_TZ).date()
    _rows, sums = _build_day_report(today)
    return render(request, 'staff/close_day.html', {'report_date': today, 'sums': sums})


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
    return render(request, 'staff/member_detail.html', {'member': member, 'subscriptions': subscriptions, 'ledger': ledger, 'sessions': sessions})


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
        if minutes_val > 0:
            MemberCreditLedger.objects.create(member=member, subscription=sub, change_type='add_minutes', minutes_delta=minutes_val, notes='إضافة دقائق تلقائية من الاشتراك', created_by=request.user)
        if credit_val > 0:
            MemberCreditLedger.objects.create(member=member, subscription=sub, change_type='add_credit', credit_delta_syp=credit_val, notes='إضافة رصيد تلقائي من الاشتراك', created_by=request.user)
    return redirect('staff_member_detail', member_id=member.public_code)


@login_required
def staff_internet(request):
    _assert_staff_capability(request.user, 'members/internet')
    active_sessions = InternetSession.objects.select_related('member', 'package').filter(status='active').order_by('-start_time')
    recent_sessions = InternetSession.objects.select_related('member', 'package').exclude(status='active').order_by('-updated_at')[:50]
    packages = InternetPackage.objects.order_by('name_ar')
    members = Member.objects.order_by('-created_at')[:200]
    return render(request, 'staff/internet.html', {'active_sessions': active_sessions, 'recent_sessions': recent_sessions, 'packages': packages, 'members': members, 'now': timezone.now()})


@login_required
def staff_internet_start(request):
    _assert_staff_capability(request.user, 'members/internet')
    if request.method != 'POST':
        return redirect('staff_internet')
    member = None
    member_id = request.POST.get('member')
    if member_id:
        member = Member.objects.filter(pk=member_id).first()
    package = get_object_or_404(InternetPackage, pk=request.POST.get('package'))
    errors = {}
    customer_phone = _validate_phone_input(request.POST.get('customer_phone', ''), 'هاتف الزبون', errors, required=False)
    start_time = _parse_local_dt_or_error(request.POST.get('start_time', ''), 'وقت البدء', errors, default=timezone.now())
    if errors:
        active_sessions = InternetSession.objects.select_related('member', 'package').filter(status='active').order_by('-start_time')
        recent_sessions = InternetSession.objects.select_related('member', 'package').exclude(status='active').order_by('-updated_at')[:50]
        packages = InternetPackage.objects.order_by('name_ar')
        members = Member.objects.order_by('-created_at')[:200]
        return render(request, 'staff/internet.html', {'active_sessions': active_sessions, 'recent_sessions': recent_sessions, 'packages': packages, 'members': members, 'now': timezone.now(), 'errors': errors, 'form_values': request.POST})
    InternetSession.objects.create(member=member, package=package, customer_name=request.POST.get('customer_name', '').strip(), customer_phone=customer_phone, start_time=start_time, end_time=start_time, notes=(request.POST.get('session_type', '').strip() + ' ' + request.POST.get('notes', '').strip()).strip(), status='active')
    return redirect('staff_internet')


@login_required
def staff_internet_session(request, session_id):
    _assert_staff_capability(request.user, 'members/internet')
    session = get_object_or_404(InternetSession.objects.select_related('member', 'package'), pk=session_id)
    return render(request, 'staff/internet_session.html', {'session': session})


@login_required
def staff_internet_end(request, session_id):
    _assert_staff_capability(request.user, 'members/internet')
    if request.method != 'POST':
        return redirect('staff_internet_session', session_id=session_id)
    session = get_object_or_404(InternetSession.objects.select_related('member'), pk=session_id)
    if session.status != 'active':
        return redirect('staff_internet_session', session_id=session_id)
    now = timezone.now()
    duration = max(int((now - session.start_time).total_seconds() // 60), 0)
    session.end_time = now
    session.actual_duration_minutes = duration
    session.status = 'ended'
    session.save(update_fields=['end_time', 'actual_duration_minutes', 'status', 'updated_at'])
    if session.member_id:
        sub = _active_subscription_for_member(session.member)
        if sub and (sub.remaining_internet_minutes or 0) > 0 and duration > 0:
            used = min(sub.remaining_internet_minutes, duration)
            sub.remaining_internet_minutes = max((sub.remaining_internet_minutes or 0) - used, 0)
            sub.save(update_fields=['remaining_internet_minutes', 'updated_at'])
            MemberCreditLedger.objects.create(member=session.member, subscription=sub, change_type='use_minutes', minutes_delta=-used, notes=f'استهلاك دقائق لجلسة إنترنت #{session.id}', created_by=request.user)
    return redirect('staff_internet_session', session_id=session_id)


@login_required
def staff_wifi(request):
    _assert_staff_capability(request.user, 'members/internet')
    networks = WifiNetwork.objects.filter(is_active=True).order_by('name_ar')
    return render(request, 'staff/wifi.html', {'networks': networks})


@login_required
def staff_events(request):
    _assert_staff_capability(request.user, 'events')
    events = Event.objects.select_related('location_area').order_by('starts_at', '-created_at')
    return render(request, 'staff/events.html', {'events': events})


@login_required
def staff_event_new(request):
    _assert_staff_capability(request.user, 'events')
    table_areas = TableArea.objects.order_by('name_ar')
    if request.method == 'POST':
        title_ar = request.POST.get('title_ar', '').strip()
        starts_at_raw = request.POST.get('starts_at', '').strip()
        if not title_ar or not starts_at_raw:
            return render(request, 'staff/event_form.html', {'table_areas': table_areas, 'statuses': Event.Status.choices, 'error': 'الاسم ووقت البداية مطلوبان.'})
        errors = {}
        starts_at = _parse_local_dt_or_error(starts_at_raw, 'وقت البداية', errors, required=True)
        ends_at = _parse_local_dt_or_error(request.POST.get('ends_at', ''), 'وقت النهاية', errors, default=None)
        if starts_at and ends_at and ends_at < starts_at:
            errors['ends_at'] = 'وقت النهاية يجب أن يكون بعد أو يساوي وقت البداية.'
        capacity = _parse_int_or_error(request.POST.get('capacity'), 'السعة', errors, default=0, min_value=0)
        if errors:
            return render(request, 'staff/event_form.html', {'table_areas': table_areas, 'statuses': Event.Status.choices, 'errors': errors, 'form_values': request.POST})
        event = Event(
            title_ar=title_ar,
            title_en=request.POST.get('title_en', '').strip(),
            description_ar=request.POST.get('description_ar', '').strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            capacity=capacity or None,
            status=request.POST.get('status') or Event.Status.DRAFT,
        )
        location_area_id = request.POST.get('location_area')
        if location_area_id:
            event.location_area = TableArea.objects.filter(pk=location_area_id).first()
        event.save()
        return redirect('staff_event_detail', event_id=event.id)
    return render(request, 'staff/event_form.html', {'table_areas': table_areas, 'statuses': Event.Status.choices})


@login_required
def staff_event_detail(request, event_id):
    _assert_staff_capability(request.user, 'events')
    event = get_object_or_404(Event.objects.select_related('location_area'), pk=event_id)
    reservations = Reservation.objects.filter(event=event).select_related('table_area').order_by('reservation_date', 'start_time')
    participations = VendorParticipation.objects.filter(event=event).select_related('vendor', 'location_area', 'event').order_by('starts_at')
    legacy_participations = VendorParticipation.objects.filter(event__isnull=True, notes__icontains=event.title_ar).select_related('vendor', 'location_area').order_by('starts_at')
    return render(request, 'staff/event_detail.html', {'event': event, 'reservations': reservations, 'participations': participations, 'legacy_participations': legacy_participations})


@login_required
def staff_reservations(request):
    _assert_staff_capability(request.user, 'reservations')
    today = timezone.localdate()
    all_rows = Reservation.objects.select_related('table_area', 'event').order_by('reservation_date', 'start_time')
    today_rows = all_rows.filter(reservation_date=today).exclude(status=Reservation.Status.CANCELLED)
    upcoming_rows = all_rows.filter(reservation_date__gt=today).exclude(status__in=[Reservation.Status.CANCELLED, Reservation.Status.COMPLETED])
    past_cancelled_rows = all_rows.filter(Q(reservation_date__lt=today) | Q(status__in=[Reservation.Status.CANCELLED, Reservation.Status.COMPLETED]))
    return render(request, 'staff/reservations.html', {'today_rows': today_rows, 'upcoming_rows': upcoming_rows, 'past_cancelled_rows': past_cancelled_rows})


@login_required
def staff_reservation_new(request):
    _assert_staff_capability(request.user, 'reservations')
    events = Event.objects.order_by('starts_at')
    table_areas = TableArea.objects.order_by('name_ar')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        errors = {}
        phone = _validate_phone_input(request.POST.get('phone', ''), 'رقم الهاتف', errors, required=True)
        reservation_date_raw = request.POST.get('reservation_date', '').strip()
        start_time_raw = request.POST.get('start_time', '').strip()
        if not (name and phone and reservation_date_raw and start_time_raw):
            return render(request, 'staff/reservation_form.html', {'events': events, 'table_areas': table_areas, 'types': Reservation.ReservationType.choices, 'error': 'الاسم والهاتف والتاريخ ووقت البداية مطلوبة.'})
        party_size = _parse_int_or_error(request.POST.get('party_size') or 1, 'عدد الأشخاص', errors, default=1, min_value=1)
        if errors:
            return render(request, 'staff/reservation_form.html', {'events': events, 'table_areas': table_areas, 'types': Reservation.ReservationType.choices, 'statuses': Reservation.Status.choices, 'errors': errors, 'form_values': request.POST})
        reservation = Reservation(
            name=name, phone=phone, reservation_date=reservation_date_raw, start_time=start_time_raw,
            reservation_type=request.POST.get('reservation_type') or Reservation.ReservationType.TABLE,
            party_size=party_size,
            status=request.POST.get('status') or Reservation.Status.PENDING,
            notes=request.POST.get('notes', '').strip(),
            created_by=request.user,
        )
        end_time = request.POST.get('end_time', '').strip()
        if end_time:
            reservation.end_time = end_time
        area_id = request.POST.get('table_area')
        if area_id:
            reservation.table_area = TableArea.objects.filter(pk=area_id).first()
        event_id = request.POST.get('event')
        if event_id:
            reservation.event = Event.objects.filter(pk=event_id).first()
        reservation.save()
        return redirect('staff_reservation_detail', reservation_id=reservation.id)
    return render(request, 'staff/reservation_form.html', {'events': events, 'table_areas': table_areas, 'types': Reservation.ReservationType.choices, 'statuses': Reservation.Status.choices})


@login_required
def staff_reservation_detail(request, reservation_id):
    _assert_staff_capability(request.user, 'reservations')
    reservation = get_object_or_404(Reservation.objects.select_related('table_area', 'event'), pk=reservation_id)
    return render(request, 'staff/reservation_detail.html', {'reservation': reservation, 'statuses': Reservation.Status.choices})


@login_required
def staff_reservation_status(request, reservation_id):
    _assert_staff_capability(request.user, 'reservations')
    if request.method != 'POST':
        raise Http404()
    reservation = get_object_or_404(Reservation, pk=reservation_id)
    new_status = request.POST.get('status')
    allowed = {c[0] for c in Reservation.Status.choices}
    if new_status in allowed:
        reservation.status = new_status
        reservation.save(update_fields=['status', 'updated_at'])
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
