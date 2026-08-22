"""Menu and core operations views with thin composition over legacy flows."""

import uuid
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.urls import resolve, reverse

from accounts.permissions import user_has_capability
from core.models import InternetSession, Order, TableArea
from core.services.visit_internet import customer_packages, self_service_enabled
from core.views.staff_context import render_order_context_panel, render_payment_panel
from core.views.staff_workspace import staff_home
from internet.catalog import decorate_menu_context, fulfill_internet_items_for_order
from members.benefits import resolve_internet_price
from core.views_legacy import (
    _create_order_from_menu,
    _menu_context,
    dashboard,
    order_public,
    staff_pos,
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
    staff_cashier,
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


def _render_table_landing(request, table):
    """Render the intentionally small first screen reached from a table QR."""
    context = _menu_context(table=table, request=request)
    settings_obj = context.get('settings') or context.get('system_settings')
    visit = context.get('current_visit')
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
        active_session = (
            visit.internet_sessions.select_related('package', 'entitlement')
            .filter(status=InternetSession.Status.ACTIVE)
            .order_by('-start_time')
            .first()
        )

    context.update({
        'table': table,
        'internet_packages': packages,
        'internet_self_service_enabled': bool(settings_obj and self_service_enabled(settings_obj)),
        'internet_request_key': uuid.uuid4(),
        'active_internet_session': active_session,
        'full_menu_url': reverse('menu_table', kwargs={'qr_token': table.qr_token}) + '?view=menu',
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
    """Create the canonical order, then fulfill any legacy Internet cart line atomically."""
    try:
        with transaction.atomic():
            response = _create_order_from_menu(request, table=table)
            order = _created_order_from_response(response)
            if order is not None:
                fulfill_internet_items_for_order(order)
            return response
    except ValidationError as error:
        return _render_customer_menu(request, table=table, error=_validation_message(error))


def menu_public(request):
    if request.method == 'POST':
        return _customer_order_from_menu(request, table=None)
    return _render_customer_menu(request, table=None)


def menu_table(request, qr_token):
    table = get_object_or_404(TableArea.objects.select_related('room'), qr_token=qr_token)
    if request.method == 'POST':
        return _customer_order_from_menu(request, table=table)
    if request.GET.get('view') != 'menu':
        return _render_table_landing(request, table)
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


def staff_cashier_order(request, public_code):
    """Keep the cashier page canonical while exposing its payment form as a panel."""

    if (
        request.method == "GET"
        and request.GET.get("panel") == "payment"
        and user_has_capability(request.user, "cashier")
    ):
        return render_payment_panel(request, public_code)
    return _legacy_staff_cashier_order(request, public_code)


def staff_cashier_pay(request, public_code):
    """Delegate payment posting, then refresh the compact panel for HTMX callers."""

    panel_request = (
        request.method == "POST"
        and request.GET.get("panel") == "payment"
        and user_has_capability(request.user, "cashier")
    )
    response = _legacy_staff_cashier_pay(request, public_code)
    if panel_request and response.status_code < 400:
        return render_payment_panel(request, public_code)
    return response
