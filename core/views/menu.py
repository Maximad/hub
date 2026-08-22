"""Menu and core operations views; no member/internet/report domain logic."""

from accounts.permissions import user_has_capability
from core.views.staff_context import render_order_context_panel, render_payment_panel
from core.views.staff_workspace import staff_home
from core.views_legacy import (
    dashboard,
    menu_public,
    menu_table,
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
