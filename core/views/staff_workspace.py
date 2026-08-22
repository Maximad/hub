"""Unified, role-aware staff operations workspace.

This view intentionally composes existing operational objects instead of
reimplementing POS, cashier, visit, or order business logic.
"""

from django.db.models import Prefetch
from django.shortcuts import render

from accounts.permissions import require_staff_capability, user_has_capability
from core.models import HubVisit, Order, Payment


ACTIVE_ORDER_STATUSES = (
    Order.Status.NEW,
    Order.Status.ACCEPTED,
    Order.Status.PREPARING,
    Order.Status.READY,
)


def _payment_prefetch():
    return Prefetch(
        "payments",
        queryset=Payment.objects.filter(is_active=True, is_reversed=False),
    )


@require_staff_capability("staff_home")
def staff_home(request):
    """Make /staff/ the single daily-operations landing workspace.

    Existing domain pages remain authoritative. The workspace gives staff a
    live, contextual surface from which they can enter those flows without
    first navigating a directory of modules.
    """

    open_visits = list(
        HubVisit.objects.filter(status=HubVisit.Status.OPEN)
        .select_related("table", "table__room", "member")
        .prefetch_related(
            Prefetch(
                "orders",
                queryset=Order.objects.select_related("table")
                .prefetch_related(_payment_prefetch())
                .order_by("-created_at"),
            )
        )
        .order_by("-last_activity_at")[:12]
    )

    active_orders = list(
        Order.objects.filter(status__in=ACTIVE_ORDER_STATUSES)
        .select_related("table", "table__room", "visit")
        .prefetch_related("items", _payment_prefetch())
        .order_by("-created_at")[:16]
    )

    ready_count = sum(order.status == Order.Status.READY for order in active_orders)
    unpaid_count = sum(order.remaining_syp > 0 for order in active_orders)

    capabilities = {
        name: user_has_capability(request.user, name)
        for name in (
            "pos",
            "orders",
            "cashier",
            "internet_billing",
            "inventory",
            "finance",
            "reports",
            "settings",
        )
    }

    return render(
        request,
        "staff/home.html",
        {
            "open_visits": open_visits,
            "active_orders": active_orders,
            "workspace_stats": {
                "open_visits": len(open_visits),
                "active_orders": len(active_orders),
                "ready_orders": ready_count,
                "unpaid_orders": unpaid_count,
            },
            "workspace_caps": capabilities,
        },
    )
