from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from catalog.models import MenuSection
from core.models import Order, OrderItem, Product, TableArea


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
