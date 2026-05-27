from django.db import transaction
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.db.models import Prefetch
from django.http import Http404, HttpResponse
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from catalog.models import MenuSection
from core.models import ActivityLog, Order, OrderItem, Payment, Product, TableArea


DAMASCUS_TZ = ZoneInfo('Asia/Damascus')


def _parse_report_date(raw_date):
    if raw_date:
        try:
            return datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            pass
    return datetime.now(DAMASCUS_TZ).date()


def _day_range_utc(report_date):
    local_start = datetime.combine(report_date, time.min).replace(tzinfo=DAMASCUS_TZ)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(ZoneInfo('UTC')), local_end.astimezone(ZoneInfo('UTC'))


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
        for p in order.payments.all():
            methods[p.method] = methods.get(p.method, 0) + p.amount_syp
        is_paid = remaining == 0
        if order.status == Order.Status.CANCELLED:
            sums['cancelled_count'] += 1
        if order.status == Order.Status.SERVED or is_paid:
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


def _menu_context(table=None):
    products_qs = Product.objects.filter(is_available=True, visible_on_qr=True).order_by('sort_order', 'name_ar')
    sections = (
        MenuSection.objects.filter(is_active=True, visible_on_qr=True)
        .prefetch_related(Prefetch('products', queryset=products_qs))
        .order_by('sort_order', 'name_ar')
    )
    section_products = []
    for section in sections:
        products = list(section.products.all())
        if products:
            section_products.append((section, products))
    return {'table': table, 'section_products': section_products}


def menu_public(request):
    if request.method == 'POST':
        return _create_order_from_menu(request, table=None)
    return render(request, 'menu/menu.html', _menu_context())


def menu_table(request, qr_token):
    table = get_object_or_404(TableArea, qr_token=qr_token)
    if request.method == 'POST':
        return _create_order_from_menu(request, table=table)
    return render(request, 'menu/menu.html', _menu_context(table=table))


def _create_order_from_menu(request, table=None):
    products = Product.objects.filter(is_available=True, visible_on_qr=True, orderable_on_qr=True)
    selected = []
    for p in products:
        qty_raw = request.POST.get(f'qty_{p.id}', '').strip()
        if not qty_raw:
            continue
        try:
            qty = int(qty_raw)
        except ValueError:
            continue
        if qty <= 0:
            continue
        item_note = request.POST.get(f'note_{p.id}', '').strip()
        selected.append((p, qty, item_note))

    if not selected:
        context = _menu_context(table)
        context['error'] = 'يرجى اختيار عنصر واحد على الأقل.'
        return render(request, 'menu/menu.html', context)

    customer_name = request.POST.get('customer_name', '').strip()
    customer_phone = request.POST.get('customer_phone', '').strip()
    general_note = request.POST.get('general_note', '').strip()
    table_label = table.name_ar if table else 'طلب عام / تيك أواي'
    note_parts = [f'الاسم: {customer_name}' if customer_name else '', f'الهاتف: {customer_phone}' if customer_phone else '', f'المكان: {table_label}', general_note]
    note = '\n'.join([n for n in note_parts if n])

    with transaction.atomic():
        order = Order.objects.create(table=table, status=Order.Status.NEW, notes=note)
        for product, qty, _item_note in selected:
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=qty,
                product_name_ar_snapshot=product.name_ar,
                product_name_en_snapshot=product.name_en,
                unit_price_syp_snapshot=product.price_syp,
            )
    return redirect(reverse('order_public', kwargs={'public_code': order.public_code}))


def order_public(request, public_code):
    try:
        order = Order.objects.select_related('table').prefetch_related('items__product').get(public_code=public_code)
    except Order.DoesNotExist as exc:
        raise Http404('الطلب غير موجود') from exc
    total = sum(item.quantity * item.unit_price_syp_snapshot for item in order.items.all())
    needs_confirmation = any(item.product.requires_staff_confirmation for item in order.items.all() if item.product_id)
    return render(request, 'menu/order_confirm.html', {'order': order, 'total': total, 'needs_confirmation': needs_confirmation})


def _can_access_staff(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return getattr(user, 'role', '') in {'admin', 'cashier', 'waiter', 'kitchen', 'staff'}


def _assert_staff_access(request):
    if not _can_access_staff(request.user):
        raise PermissionDenied("غير مصرح")


def _order_financials(order):
    total = sum(item.quantity * item.unit_price_syp_snapshot for item in order.items.all())
    paid = sum(p.amount_syp for p in order.payments.exclude(method=Payment.Method.UNPAID))
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


@login_required
def staff_home(request):
    _assert_staff_access(request)
    return render(request, 'staff/home.html')


@login_required
def staff_qr_links(request):
    _assert_staff_access(request)
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    rows = []
    for table in tables:
        path = reverse('menu_table', kwargs={'qr_token': table.qr_token})
        rows.append({'table': table, 'path': path, 'full_url': request.build_absolute_uri(path)})
    return render(request, 'staff/qr_links.html', {'rows': rows})


@login_required
def staff_qr_print(request):
    _assert_staff_access(request)
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    rows = []
    for table in tables:
        path = reverse('menu_table', kwargs={'qr_token': table.qr_token})
        rows.append({'table': table, 'full_url': request.build_absolute_uri(path)})
    return render(request, 'staff/qr_print.html', {'rows': rows})


@login_required
def staff_menu_tools(request):
    _assert_staff_access(request)
    if request.method == 'POST':
        product = get_object_or_404(Product, public_code=request.POST.get('product_code'))
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
    _assert_staff_access(request)
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
    return render(request, template, {'grouped': grouped})


@login_required
def staff_order_status(request, public_code):
    _assert_staff_access(request)
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
def staff_cashier(request):
    _assert_staff_access(request)
    query = request.GET.get('q', '').strip()
    orders = Order.objects.select_related('table').prefetch_related('items', 'payments').order_by('-created_at')
    if query:
        try:
            orders = orders.filter(public_code=uuid.UUID(query))
        except ValueError:
            orders = orders.none()
    rows = []
    for order in orders[:100]:
        total, paid, remaining, payment_label = _order_financials(order)
        rows.append({'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label})
    return render(request, 'staff/cashier.html', {'rows': rows, 'query': query})


@login_required
def staff_cashier_order(request, public_code):
    _assert_staff_access(request)
    order = get_object_or_404(Order.objects.select_related('table').prefetch_related('items', 'payments'), public_code=public_code)
    total, paid, remaining, payment_label = _order_financials(order)
    methods = Payment.Method.choices
    return render(request, 'staff/cashier_order.html', {'order': order, 'total': total, 'paid': paid, 'remaining': remaining, 'payment_label': payment_label, 'methods': methods})


@login_required
def staff_cashier_pay(request, public_code):
    _assert_staff_access(request)
    if request.method != 'POST':
        raise Http404()
    order = get_object_or_404(Order, public_code=public_code)
    method = request.POST.get('method', Payment.Method.CASH)
    valid_methods = {m[0] for m in Payment.Method.choices}
    if method not in valid_methods:
        method = Payment.Method.CASH
    amount = request.POST.get('amount_syp', '0').strip()
    try:
        amount_val = int(amount)
    except ValueError:
        amount_val = 0
    if amount_val < 0:
        amount_val = 0
    Payment.objects.create(order=order, amount_syp=amount_val, method=method)
    return redirect('staff_cashier_order', public_code=order.public_code)


@login_required
def staff_reports_home(request):
    _assert_staff_access(request)
    today = datetime.now(DAMASCUS_TZ).date()
    _rows, sums = _build_day_report(today)
    return render(request, 'staff/reports_home.html', {'report_date': today, 'sums': sums})


@login_required
def staff_reports_day(request):
    _assert_staff_access(request)
    report_date = _parse_report_date(request.GET.get('date', '').strip())
    rows, sums = _build_day_report(report_date)
    return render(request, 'staff/reports_day.html', {'report_date': report_date, 'rows': rows, 'sums': sums})


@login_required
def staff_reports_day_csv(request):
    _assert_staff_access(request)
    report_date = _parse_report_date(request.GET.get('date', '').strip())
    rows, _sums = _build_day_report(report_date)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="report-{report_date.isoformat()}.csv"'
    response.write('public_code,created_at,table,status,total,paid,remaining,payment_methods\n')
    for row in rows:
        order = row['order']
        methods_txt = '; '.join(f'{k}:{v}' for k, v in row['methods'].items())
        table_name = order.table.name_ar if order.table_id else 'طلب عام / تيك أواي'
        response.write(f'{order.public_code},{order.created_at.isoformat()},{table_name},{order.get_status_display()},{row["total"]},{row["paid"]},{row["remaining"]},"{methods_txt}"\n')
    return response


@login_required
def staff_close_day(request):
    _assert_staff_access(request)
    today = datetime.now(DAMASCUS_TZ).date()
    _rows, sums = _build_day_report(today)
    return render(request, 'staff/close_day.html', {'report_date': today, 'sums': sums})
