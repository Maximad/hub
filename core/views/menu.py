"""Menu and core operations views with thin composition over legacy flows."""

import uuid
from urllib.parse import urlsplit

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import resolve, reverse

from accounts.permissions import user_has_capability
from core.models import ActivityLog, HubVisit, Order, TableArea
from core.services.table_visit_access import (
    assert_pin_attempt_allowed,
    clear_pin_failures,
    create_table_visit,
    find_open_visit_by_pin,
    record_pin_failure,
    resolve_table_number,
    visit_join_pin,
)
from core.services.visit_internet import customer_packages, metered_customer_error, self_service_enabled
from core.services.visit_internet_devices import active_browser_session
from core.services.visits import issue_visit_credential, resolve_visit_credential, set_visit_cookie
from core.settings_helpers import get_system_settings
from core.views.staff_cashier_visits import (
    staff_cashier as _visit_staff_cashier,
    staff_cashier_visit,
    staff_cashier_visit_pay,
    staff_cashier_visit_receipt,
    staff_cashier_visit_receipt_thermal,
    staff_cashier_visit_settle,
)
from core.views.staff_context import render_order_context_panel, render_payment_panel
from core.views.staff_workspace import staff_home
from internet.catalog import decorate_menu_context, fulfill_internet_items_for_order
from members.benefits import resolve_internet_price
from members.services import resolve_member_from_request
from core.views_legacy import (
    _create_order_from_menu,
    _menu_context,
    dashboard,
    order_public,
    staff_qr_links,
    staff_qr_print,
    staff_menu_tools,
    staff_menu_tools_preview,
    staff_menu_tools_apply,
    staff_modifiers,
    staff_orders,
    staff_delivery,
    staff_order_status,
    staff_order_edit as _legacy_staff_order_edit,
    staff_order_edit_add_item,
    staff_order_edit_update_item,
    staff_order_edit_remove_item,
    staff_cashier_order as _legacy_staff_cashier_order,
    staff_cashier_pay as _legacy_staff_cashier_pay,
    staff_cashier_discount,
    staff_food_lab,
    order_qr,
    table_qr,
    staff_order_receipt,
    staff_order_receipt_thermal,
    staff_order_prep_ticket,
    staff_order_delivery_ticket,
)
from core.views.staff_pos_v2 import staff_pos


def _validation_message(error):
    if hasattr(error, 'message_dict'):
        return ' '.join(
            message
            for field_messages in error.message_dict.values()
            for message in field_messages
        )
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _render_customer_menu(request, *, table=None, error=''):
    context = decorate_menu_context(_menu_context(table=table, request=request), table=table)
    if error:
        context['error'] = error
        context['form_values'] = request.POST
    return render(request, 'menu/menu.html', context)


def _bound_visit_for_table(request, table):
    credential = resolve_visit_credential(request)
    if credential and credential.visit.table_id == table.pk:
        return credential.visit
    return None


def _render_table_landing(request, table, *, access_error=''):
    """Render the one-time account choice shown before the two-screen customer UI."""
    context = _menu_context(table=table, request=request)
    settings_obj = context.get('settings') or context.get('system_settings')
    visit_access_enabled = bool(settings_obj and settings_obj.customer_visits_enabled)
    visit = context.get('current_visit') if visit_access_enabled else None
    member_context = context.get('member_context')
    member = visit.member if visit and visit.member_id else (
        member_context.member if member_context else None
    )

    packages = []
    if settings_obj and self_service_enabled(settings_obj):
        packages = customer_packages(member)
        for package in packages:
            package.customer_price_syp = int(resolve_internet_price(member, package)[0])

    active_session = None
    if visit:
        credential = resolve_visit_credential(request, touch=False)
        if credential and credential.visit_id == visit.pk:
            active_session = active_browser_session(credential)

    metered_error = metered_customer_error(settings_obj, member) if settings_obj else 'غير متاح'
    open_visit_count = (
        HubVisit.objects.filter(table=table, status=HubVisit.Status.OPEN).count()
        if visit_access_enabled else 0
    )
    show_visit_access = bool(visit_access_enabled)
    context.update({
        'table': table,
        'internet_packages': packages,
        'internet_self_service_enabled': bool(settings_obj and self_service_enabled(settings_obj)),
        'internet_metered_available': not metered_error,
        'internet_metered_unavailable_reason': metered_error or '',
        'internet_metered_rate_syp': int(getattr(settings_obj, 'default_rate_per_hour_syp', 0) or 0),
        'internet_metered_minimum_minutes': int(getattr(settings_obj, 'default_minimum_minutes', 0) or 0),
        'internet_metered_requires_phone': bool(getattr(settings_obj, 'require_phone_for_guest_session', False) and member is None),
        'internet_request_key': uuid.uuid4(),
        'active_internet_session': active_session,
        'full_menu_url': reverse('menu_table', kwargs={'qr_token': table.qr_token}),
        'open_visit_count': open_visit_count,
        'show_visit_access': show_visit_access,
        'visit_join_pin': visit_join_pin(visit) if visit else '',
        'visit_access_error': access_error if visit_access_enabled else '',
        'table_number_entry_url': reverse('menu_public') + '?table_entry=1',
    })
    return render(request, 'menu/table_landing.html', context)


def _created_order_from_response(response):
    if not (300 <= response.status_code < 400):
        return None
    location = response.get('Location', '')
    if not location:
        return None
    try:
        match = resolve(urlsplit(location).path)
    except Exception:
        return None
    if match.url_name != 'order_public':
        return None
    public_code = match.kwargs.get('public_code')
    return Order.objects.select_related('visit', 'member', 'table').filter(public_code=public_code).first()


def _customer_order_from_menu(request, *, table=None):
    """Create the canonical order while keeping visit customers on the menu screen."""
    try:
        with transaction.atomic():
            response = _create_order_from_menu(request, table=table)
            order = _created_order_from_response(response)
            if order is not None:
                fulfill_internet_items_for_order(order)
                if table is not None and order.visit_id:
                    messages.success(request, f'تم إرسال الطلب {order.display_number}. يمكنك متابعته من «جلستي».')
                    return redirect('menu_table', qr_token=table.qr_token)
            return response
    except ValidationError as error:
        return _render_customer_menu(request, table=table, error=_validation_message(error))


def _render_table_number_entry(request, *, error=''):
    return render(request, 'menu/table_number_entry.html', {
        'table_number_error': error,
        'table_number_value': request.GET.get('table_number', ''),
    })


def menu_public(request):
    if request.method == 'POST':
        return _customer_order_from_menu(request, table=None)

    if request.GET.get('table_entry') == '1' or 'table_number' in request.GET:
        raw_number = request.GET.get('table_number', '').strip()
        if raw_number:
            try:
                table = resolve_table_number(raw_number)
            except ValidationError as exc:
                return _render_table_number_entry(request, error=_validation_message(exc))
            return redirect('menu_table', qr_token=table.qr_token)
        return _render_table_number_entry(request)

    return _render_customer_menu(request, table=None)


def _handle_table_visit_action(request, table, action):
    member_context = resolve_member_from_request(request)
    member = member_context.member if member_context else None

    if action == 'create':
        visit = create_table_visit(table, member=member)
        _credential, raw_token = issue_visit_credential(visit)
        ActivityLog.objects.create(action='visit.created', details={
            'visit_id': visit.pk,
            'table_id': table.pk,
            'source': 'table_account_selection',
        })
        ActivityLog.objects.create(action='visit.browser_bound', details={
            'visit_id': visit.pk,
            'table_id': table.pk,
            'binding': 'new_separate_account',
        })
        messages.success(request, f'تم فتح حسابك. رمز مشاركة الجلسة هو {visit_join_pin(visit)}.')
    elif action == 'join':
        try:
            assert_pin_attempt_allowed(request, table)
            visit = find_open_visit_by_pin(table, request.POST.get('pin', ''))
        except ValidationError as exc:
            if 'مؤقتاً' not in _validation_message(exc):
                record_pin_failure(request, table)
            return _render_table_landing(request, table, access_error=_validation_message(exc))
        clear_pin_failures(request, table)
        _credential, raw_token = issue_visit_credential(visit)
        ActivityLog.objects.create(action='visit.browser_bound', details={
            'visit_id': visit.pk,
            'table_id': table.pk,
            'binding': 'pin_join',
        })
        messages.success(request, 'تم الانضمام إلى الحساب المشترك.')
    else:
        return _render_table_landing(request, table, access_error='اختر طريقة الدخول إلى الطاولة.')

    # Account choice is part of welcome. Once chosen, customers enter the menu
    # directly and thereafter move only between Menu and Session.
    response = redirect('menu_table', qr_token=table.qr_token)
    return set_visit_cookie(response, raw_token)


def menu_table(request, qr_token):
    table = get_object_or_404(TableArea.objects.select_related('room'), qr_token=qr_token)
    settings_obj = get_system_settings()
    visit_access_enabled = bool(settings_obj.customer_visits_enabled)
    visit = _bound_visit_for_table(request, table) if visit_access_enabled else None

    if request.method == 'POST':
        action = request.POST.get('visit_action', '').strip()
        if action and visit_access_enabled:
            return _handle_table_visit_action(request, table, action)
        # A customer may order only after explicitly choosing which bill this
        # browser belongs to. Feature-disabled deployments retain the legacy flow.
        if visit_access_enabled and visit is None:
            return _render_table_landing(
                request,
                table,
                access_error='اختر أولاً الانضمام إلى حساب موجود أو فتح حساب منفصل.',
            )
        return _customer_order_from_menu(request, table=table)

    if visit_access_enabled and visit is None:
        return _render_table_landing(request, table)

    # The table URL is the canonical Menu screen after welcome. The old
    # ?view=menu query remains harmless for existing QR/bookmarks but is no
    # longer required and there is no post-welcome table landing screen.
    return _render_customer_menu(request, table=table)


def staff_order_edit(request, public_code):
    """Keep the full edit flow canonical while exposing a read-only drawer panel."""

    if (
        request.method == "GET"
        and request.GET.get("panel") == "context"
        and user_has_capability(request.user, "order_edit")
    ):
        return render_order_context_panel(request, public_code)
    return _legacy_staff_order_edit(request, public_code)


def staff_cashier(request):
    """Cashier landing groups table/visit orders into one payable account."""
    return _visit_staff_cashier(request)


def _cashier_target(public_code):
    order = Order.objects.select_related('visit').filter(public_code=public_code).first()
    if order:
        return order, order.visit
    visit = HubVisit.objects.filter(public_code=public_code).first()
    return None, visit


def staff_cashier_order(request, public_code):
    """Resolve an order cashier link to its visit account when one exists."""
    order, visit = _cashier_target(public_code)
    if visit:
        if request.GET.get('receipt') == 'thermal':
            return staff_cashier_visit_receipt_thermal(request, visit.public_code)
        if request.GET.get('receipt') == '1':
            return staff_cashier_visit_receipt(request, visit.public_code)
        return staff_cashier_visit(request, visit.public_code)
    if order is None:
        return _legacy_staff_cashier_order(request, public_code)
    if (
        request.method == "GET"
        and request.GET.get("panel") == "payment"
        and user_has_capability(request.user, "cashier")
    ):
        return render_payment_panel(request, public_code)
    return _legacy_staff_cashier_order(request, public_code)


def staff_cashier_pay(request, public_code):
    """Post against the visit account when the target belongs to a visit."""
    order, visit = _cashier_target(public_code)
    if visit:
        if request.POST.get('action') == 'settle_close':
            return staff_cashier_visit_settle(request, visit.public_code)
        return staff_cashier_visit_pay(request, visit.public_code)

    panel_request = (
        request.method == "POST"
        and request.GET.get("panel") == "payment"
        and user_has_capability(request.user, "cashier")
    )
    response = _legacy_staff_cashier_pay(request, public_code)
    if panel_request and response.status_code < 400:
        return render_payment_panel(request, public_code)
    return response
