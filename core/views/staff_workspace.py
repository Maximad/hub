"""Unified, role-aware staff operations workspace.

The operations workspace is the everyday front-of-house surface. It composes
existing visit, POS, cashier and Internet logic rather than duplicating those
domain services.
"""

from datetime import datetime, time

from django.conf import settings
from django.db.models import Prefetch, Q
from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import require_staff_capability, user_has_capability
from core.models import HubVisit, InternetSession, Member, Order, Payment, TableArea
from operations.services import current_business_date


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


def _workspace_order_queryset():
    return (
        Order.objects.select_related("table", "table__room")
        .prefetch_related("items", "discounts", _payment_prefetch())
        .order_by("-created_at")
    )


def _current_business_day_start():
    business_date = current_business_date()
    cutoff = int(getattr(settings, 'BUSINESS_DAY_CUTOFF_HOUR', 4))
    return timezone.make_aware(
        datetime.combine(business_date, time(hour=cutoff)),
        timezone=timezone.get_current_timezone(),
    )


@require_staff_capability("staff_home")
def staff_home(request):
    """Make /staff/ the single daily front-of-house operations workspace."""

    day_start = _current_business_day_start()
    open_visits = list(
        HubVisit.objects.filter(status=HubVisit.Status.OPEN)
        .select_related("table", "table__room", "member")
        .prefetch_related(
            Prefetch(
                "orders",
                queryset=_workspace_order_queryset().exclude(status=Order.Status.CANCELLED),
                to_attr="workspace_orders",
            ),
            "internet_sessions",
        )
        .order_by("-last_activity_at")
    )
    live_visit_rows = []
    stale_visit_rows = []
    for visit in open_visits:
        orders = visit.workspace_orders
        active_internet_count = sum(
            session.status == InternetSession.Status.ACTIVE
            for session in visit.internet_sessions.all()
        )
        gross_syp = sum(order.total_syp for order in orders)
        remaining_syp = sum(order.remaining_syp for order in orders)
        active_order_count = sum(order.status in ACTIVE_ORDER_STATUSES for order in orders)
        row = {
            "visit": visit,
            "gross_syp": gross_syp,
            "remaining_syp": remaining_syp,
            "latest_order": orders[0] if orders else None,
            "order_count": len(orders),
            "active_order_count": active_order_count,
            "active_internet_count": active_internet_count,
            "has_unpaid": remaining_syp > 0,
        }
        if visit.last_activity_at and visit.last_activity_at < day_start:
            stale_visit_rows.append(row)
        else:
            live_visit_rows.append(row)

    # Orders from stale visits belong in the stale-account review rather than the
    # live floor. A newly active order updates its visit and naturally returns to
    # the live workspace.
    active_orders = list(
        _workspace_order_queryset()
        .filter(status__in=ACTIVE_ORDER_STATUSES)
        .filter(
            Q(visit__isnull=True, created_at__gte=day_start)
            | Q(visit__status=HubVisit.Status.OPEN, visit__last_activity_at__gte=day_start)
        )
        .select_related("visit")[:20]
    )

    ready_count = sum(order.status == Order.Status.READY for order in active_orders)

    # One current open visit is one payable account. Stale unpaid accounts remain
    # visible in their dedicated review section rather than inflating the live floor.
    unpaid_visit_accounts = sum(row["remaining_syp"] > 0 for row in live_visit_rows)
    standalone_orders = list(
        _workspace_order_queryset()
        .filter(visit__isnull=True, created_at__gte=day_start)
        .exclude(status=Order.Status.CANCELLED)
    )
    unpaid_standalone_accounts = sum(order.remaining_syp > 0 for order in standalone_orders)
    unpaid_count = unpaid_visit_accounts + unpaid_standalone_accounts

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
            "visit_rows": live_visit_rows[:20],
            "stale_visit_rows": stale_visit_rows[:40],
            "active_orders": active_orders,
            "workspace_stats": {
                "open_visits": len(live_visit_rows),
                "stale_visits": len(stale_visit_rows),
                "active_orders": len(active_orders),
                "ready_orders": ready_count,
                "unpaid_orders": unpaid_count,
            },
            "workspace_caps": capabilities,
            "workspace_business_day_start": day_start,
            # Used by the inline new-account form. Routine staff should not have
            # to leave Operations simply to create a customer account.
            "workspace_tables": TableArea.objects.select_related("room").order_by("room__name_ar", "name_ar"),
            "workspace_members": Member.objects.order_by("name_ar")[:200],
        },
    )
