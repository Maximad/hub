"""Compact, role-aware staff context panels.

These renderers intentionally reuse existing order and cashier business logic. They
provide progressive UI fragments only; mutations remain owned by the existing
legacy staff handlers and posting services.
"""

from django.shortcuts import get_object_or_404, render

from accounts.permissions import user_has_capability
from core.currency_forms import CurrencyEntryFormService
from core.models import Order, OrderDiscount, Payment
from core.views_legacy import _order_financials


def _order_queryset():
    return (
        Order.objects.select_related(
            "table",
            "table__room",
            "visit",
            "visit__table",
            "visit__table__room",
            "visit__member",
        )
        .prefetch_related("items", "payments", "discounts")
    )


def _financial_context(order):
    total, paid, remaining, payment_label = _order_financials(order)
    return {
        "total": total,
        "paid": paid,
        "remaining": remaining,
        "payment_label": payment_label,
    }


def render_order_context_panel(request, public_code):
    """Render a compact order summary for the shared staff drawer."""

    order = get_object_or_404(_order_queryset(), public_code=public_code)
    context = {
        "order": order,
        **_financial_context(order),
        "order_caps": {
            "edit": user_has_capability(request.user, "order_edit"),
            "cashier": user_has_capability(request.user, "cashier"),
            "orders": user_has_capability(request.user, "orders"),
            "kitchen": user_has_capability(request.user, "kitchen_board"),
        },
    }
    return render(request, "staff/_order_panel.html", context)


def render_payment_panel(request, public_code):
    """Render the compact cashier form while keeping payment posting canonical."""

    order = get_object_or_404(_order_queryset(), public_code=public_code)
    financial = _financial_context(order)
    context = {
        "order": order,
        **financial,
        "methods": Payment.Method.choices,
        "discount_types": OrderDiscount.DiscountType.choices,
        "payment_amount_default": financial["remaining"],
        "currency_component": CurrencyEntryFormService(
            request,
            operation="payment",
        ).context,
    }
    return render(request, "staff/_payment_panel.html", context)
