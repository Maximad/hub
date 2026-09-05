"""Launch-ready POS view.

The POS must reflect the same public menu visibility as the customer menu while
retaining POS-specific orderability controls. Keeping this view outside the
legacy module lets the launch sprint simplify the high-frequency surface without
rewriting unrelated legacy operations.
"""

from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.permissions import require_staff_capability
from core.models import ActivityLog, HubVisit, Member, Order, TableArea
from core.settings_helpers import get_page_setting, get_system_settings
from core.views_legacy import (
    _create_order_from_selected_items,
    _order_location_note,
    _section_products_for_ordering,
    _selected_order_items_from_post,
    _subtotal_for_selected,
    _validate_delivery_details,
    _validate_fulfillment_mode,
    _validate_phone_input,
)
from members.services import get_active_member_context


def _pos_catalog():
    """Return the menu-visible catalog that is also usable from staff POS.

    Public menu visibility is the baseline source of truth. POS-specific flags
    may further restrict an item, but they can no longer make a customer-hidden
    section/product reappear in POS. Unsectioned products are intentionally
    omitted because the public menu omits them as well.
    """

    return _section_products_for_ordering(
        product_filter={
            'is_available': True,
            'visible_on_qr': True,
            'visible_on_pos': True,
            'orderable_on_pos': True,
        },
        section_filter={'visible_on_qr': True},
        include_unsectioned=False,
        media_filter={'display_on_pos': True},
    )


@require_staff_capability('pos')
def staff_pos(request):
    tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    section_products = _pos_catalog()
    products = [product for _section, section_items in section_products for product in section_items]

    member_query = request.GET.get('member_q', '').strip()
    member_rows = []
    if member_query:
        member_rows = list(
            Member.objects.filter(Q(name_ar__icontains=member_query) | Q(phone__icontains=member_query))
            .order_by('-created_at')[:8]
        )

    settings = get_system_settings()
    requested_visit = (
        HubVisit.objects.select_related('table').filter(
            public_code=request.GET.get('visit'),
            status=HubVisit.Status.OPEN,
        ).first()
        if request.GET.get('visit') else None
    )
    requested_table_id = str(requested_visit.table_id) if requested_visit and requested_visit.table_id else ''
    initial_form_values = (
        {
            'table_id': requested_table_id,
            'fulfillment_mode': Order.FulfillmentMode.TABLE,
        }
        if requested_table_id else {}
    )
    context = {
        'section_products': section_products,
        'tables': tables,
        'member_query': member_query,
        'member_rows': member_rows,
        'settings': settings,
        'open_visits': HubVisit.objects.filter(status=HubVisit.Status.OPEN)
        .select_related('table').order_by('-last_activity_at'),
        'selected_visit_id': str(requested_visit.pk) if requested_visit else '',
        'selected_table_id': requested_table_id,
        'form_values': initial_form_values,
        'page_setting': get_page_setting(
            'staff_pos',
            'نقطة البيع',
            'POS',
            'إدخال طلبات داخل المكان أو على الطاولات.',
            'Create table or in-space orders.',
        ),
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

        visit_id = request.POST.get('visit_id', '').strip()
        visit = (
            HubVisit.objects.select_related('table').filter(
                pk=visit_id,
                status=HubVisit.Status.OPEN,
            ).first()
            if visit_id.isdigit() else None
        )
        if visit_id and visit is None:
            validation_errors.append('الجلسة المحددة غير صالحة أو مغلقة.')

        if visit and visit.table_id:
            if table is not None and table.pk != visit.table_id:
                validation_errors.append('الطاولة المحددة لا تطابق طاولة الحساب المفتوح.')
            table = visit.table
            table_id = str(visit.table_id)
            posted_mode = request.POST.get('fulfillment_mode', '').strip()
            if posted_mode and posted_mode != Order.FulfillmentMode.TABLE:
                validation_errors.append('طريقة الطلب لا تطابق الحساب المرتبط بطاولة.')
            fulfillment_mode = Order.FulfillmentMode.TABLE
            fulfillment_error = ''
        else:
            fulfillment_mode, fulfillment_error = _validate_fulfillment_mode(
                request, settings, table=table, allow_table=bool(table)
            )
            if table:
                fulfillment_mode = Order.FulfillmentMode.TABLE
        if fulfillment_error:
            validation_errors.append(fulfillment_error)
        if request.POST.get('fulfillment_mode') == Order.FulfillmentMode.TABLE and not table:
            validation_errors.append('يرجى اختيار طاولة لهذا الطلب.')
        if not selected:
            context.update({
                'error': 'يرجى اختيار عنصر واحد على الأقل.',
                'form_values': request.POST,
                'selected_table_id': table_id,
                'selected_visit_id': visit_id,
            })
            return render(request, 'staff/pos.html', context)

        subtotal = _subtotal_for_selected(selected)
        delivery_data, delivery_errors = _validate_delivery_details(
            request, settings, fulfillment_mode, subtotal
        )
        validation_errors.extend(delivery_errors)

        errors = {}
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = _validate_phone_input(
            request.POST.get('customer_phone', ''),
            'رقم الهاتف',
            errors,
            required=(
                fulfillment_mode == Order.FulfillmentMode.DELIVERY
                and (settings.require_delivery_phone or settings.require_phone_for_delivery)
            ),
        )
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
            context.update({
                'error': ' '.join(validation_errors),
                'form_values': request.POST,
                'selected_table_id': table_id,
                'selected_visit_id': visit_id,
            })
            return render(request, 'staff/pos.html', context)

        general_note = request.POST.get('general_note', '').strip()
        service_mode = (
            Order.ServiceMode.TABLE
            if fulfillment_mode == Order.FulfillmentMode.TABLE
            else (
                Order.ServiceMode.TAKEAWAY
                if fulfillment_mode == Order.FulfillmentMode.TAKEAWAY
                else Order.ServiceMode.DINE_IN
            )
        )
        table_label = _order_location_note(table, service_mode, fulfillment_mode)
        note_parts = [
            'Source: staff/pos',
            f'المكان: {table_label}',
            f'الاسم: {customer_name}' if customer_name else '',
            f'الهاتف: {customer_phone}' if customer_phone else '',
            f'العضو: {member.name_ar} / {member.phone}' if member else '',
            general_note,
        ]
        order = _create_order_from_selected_items(
            table,
            selected,
            note_parts,
            status=Order.Status.NEW,
            service_mode=service_mode,
            fulfillment_mode=fulfillment_mode,
            delivery_data=delivery_data,
            member_context=member_context,
            visit=visit,
        )
        if visit:
            HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=timezone.now())
            ActivityLog.objects.create(
                actor=request.user,
                action='visit.order_linked',
                details={'visit_id': visit.pk, 'order_id': order.pk, 'source': 'staff_pos'},
            )
        ActivityLog.objects.create(
            actor=request.user,
            action='staff_pos_order_created',
            details={
                'order_public_code': str(order.public_code),
                'table_id': table.id if table else None,
                'fulfillment_mode': fulfillment_mode,
            },
        )
        return redirect('staff_cashier_order', public_code=order.public_code)

    return render(request, 'staff/pos.html', context)