"""Unified, role-aware staff operations workspace.

The operations workspace is the everyday front-of-house surface. It composes
existing visit, POS, cashier and Internet logic rather than duplicating those
domain services.
"""

from datetime import datetime, time

from django.conf import settings
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import get_staff_capabilities, is_cashier, is_owner_or_admin, require_staff_capability
from core.models import HubVisit, InternetSession, Member, Order, Payment, TableArea
from operations.services import current_business_date
from core.services.visits import customer_visit_queryset
from core.services.staff_attention import get_staff_attention
from internet.guest_wifi import get_guest_wifi_policy, current_venue_code
from operations.models import BusinessDay, BusinessDayTask


ACTIVE_ORDER_STATUSES = (
    Order.Status.NEW,
    Order.Status.ACCEPTED,
    Order.Status.PREPARING,
    Order.Status.READY,
)


# Navigation only: use existing routes and effective capabilities. Each route also
# enforces its own authorization, which remains the security boundary.
DAILY_ACTIONS = {
    "order": ("بيع / طلب جديد", "طلب جديد للزبون من نقطة البيع", "staff_pos", "pos"),
    "cashier": ("قبض ودفع", "تحصيل المدفوعات ومراجعة المتبقي", "staff_cashier", "cashier"),
    "prep": ("فتح لوحة التحضير", "طلبات المطبخ والبار", "staff_kitchen", "kitchen_board"),
    "count": ("جرد المخزون", "أدخل الكمية الموجودة فعلياً", "staff_inventory_stock_count", "inventory_stock_count"),
    "purchase": ("تسجيل شراء", "مواد مشتراة ثم استلامها", "staff_inventory_purchase_new", "inventory"),
    "expense": ("تسجيل مصروف", "مسودة أو دفع فعلي", "staff_finance_expense_new", "finance"),
    "tasks": ("مهام الفريق", "ما يحتاج متابعة اليوم", "staff_operational_tasks", "staff_home"),
    "inventory": ("المواد والنواقص", "عرض المخزون المسجل", "staff_inventory_home", "inventory"),
    "internet": ("الإنترنت", "الباقات والجلسات", "staff_internet", "internet_billing"),
    "visits": ("الجلسات", "عرض الحسابات المفتوحة", "staff_visits", "visit_manage"),
    "reservations": ("الحجوزات", "تنسيق وصول الزبائن", "staff_reservations", "reservations"),
}


def _daily_workspace_actions(user, capabilities):
    """At most four top tasks; remaining permitted links stay discoverable.

    This is a presentation-only shortlist. Do not equate generic 'inventory'
    read access with permission to create purchases or approve stock counts.
    """
    if capabilities.get("kitchen_board") and not capabilities.get("pos"):
        order = ("prep", "tasks", "inventory", "count", "cashier", "internet")
    elif is_cashier(user):
        # Four core daily operations first. Collection remains a prominent
        # separate action, not hidden from a cashier's actual workflow.
        order = ("order", "purchase", "count", "internet", "cashier", "tasks", "expense", "prep", "visits")
    elif is_owner_or_admin(user):
        order = ("order", "purchase", "count", "internet", "cashier", "tasks", "expense", "prep", "visits")
    else:
        order = ("order", "cashier", "count", "purchase", "prep", "tasks",
                 "expense", "inventory", "internet", "visits", "reservations")

    actions = []
    for key in order:
        title, hint, url_name, capability = DAILY_ACTIONS[key]
        if not capabilities.get(capability):
            continue
        if key == "purchase" and not (is_owner_or_admin(user) or is_cashier(user)):
            continue
        actions.append({"key": key, "title": title, "hint": hint, "url": reverse(url_name)})
    return actions[:4], actions[4:]


def _payment_prefetch():
    return Prefetch(
        "payments",
        queryset=Payment.objects.filter(is_active=True, is_reversed=False),
    )


def _workspace_order_queryset():
    return (
        Order.objects.select_related("table", "table__room", "entered_by")
        .prefetch_related("items", "discounts", "consumptions", _payment_prefetch())
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
        customer_visit_queryset(HubVisit.objects.filter(status=HubVisit.Status.OPEN))
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
        active_metered_count = sum(
            session.status == InternetSession.Status.ACTIVE
            and session.billing_mode == InternetSession.BillingMode.OPEN_METERED
            for session in visit.internet_sessions.all()
        )
        gross_syp = sum(order.total_syp for order in orders)
        remaining_syp = sum(order.remaining_syp for order in orders)
        active_order_count = sum(order.status in ACTIVE_ORDER_STATUSES for order in orders)
        # A free Wi-Fi session is a technical record, not a payable account.
        # Keep a paid time-metered session visible even before its final charge.
        if remaining_syp <= 0 and active_metered_count == 0:
            continue
        row = {
            "visit": visit,
            "gross_syp": gross_syp,
            "remaining_syp": remaining_syp,
            "latest_order": orders[0] if orders else None,
            "order_count": len(orders),
            "active_order_count": active_order_count,
            "active_internet_count": active_internet_count,
            "active_metered_count": active_metered_count,
            "has_unpaid": remaining_syp > 0,
        }
        if visit.last_activity_at and visit.last_activity_at < day_start:
            stale_visit_rows.append(row)
        else:
            live_visit_rows.append(row)

    # Fulfillment handovers are real outstanding work across the 04:00 cutoff.
    # A paid READY order must not disappear just because its visit is idle or
    # because the order was entered on a previous business date. Closed-visit
    # and served/cancelled work stays outside the live fulfillment queue.
    attention_queryset = (
        _workspace_order_queryset()
        .filter(status__in=ACTIVE_ORDER_STATUSES)
        .filter(Q(visit__isnull=True) | Q(visit__status=HubVisit.Status.OPEN))
        .select_related("visit")
    )
    # Count before the display cap; handoff-ready work goes first, then new work.
    active_orders_count = attention_queryset.count()
    today_active_count = attention_queryset.filter(created_at__gte=day_start).count()
    today_ready_count = attention_queryset.filter(created_at__gte=day_start, status=Order.Status.READY).count()
    ready_count = attention_queryset.filter(status=Order.Status.READY).count()
    carryover_order_count = attention_queryset.filter(created_at__lt=day_start).count()
    active_orders = list(attention_queryset.order_by(
        # Presentation only: current-day work first; all old obligations remain.
        Case(
            When(created_at__gte=day_start, then=Value(0)),
            default=Value(1), output_field=IntegerField(),
        ),
        Case(
            When(status=Order.Status.READY, then=Value(0)),
            When(status=Order.Status.NEW, then=Value(1)),
            When(status=Order.Status.ACCEPTED, then=Value(2)),
            When(status=Order.Status.PREPARING, then=Value(3)),
            default=Value(4), output_field=IntegerField(),
        ), "created_at", "pk",
    )[:20])
    for order in active_orders:
        order.workspace_carryover = order.created_at < day_start
        # Mirrors the existing order-edit guard. Permission and POST checks
        # remain enforced by the editing endpoint.
        order.workspace_can_edit_items = not (
            order.status == Order.Status.CANCELLED
            or bool(order.consumptions.all())
            or order.paid_syp > 0
        )
        order.workspace_next_step = {
            Order.Status.READY: "جاهز للتسليم",
            Order.Status.NEW: "طلب جديد للتحضير",
            Order.Status.ACCEPTED: "بانتظار التحضير",
            Order.Status.PREPARING: "قيد التحضير",
        }[order.status]

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
    # Served standalone orders still owing money have a collection action.
    standalone_to_collect = [
        order for order in standalone_orders
        if order.status == Order.Status.SERVED and order.remaining_syp > 0
    ]

    # Older standalone served orders remain payable even without a visit.
    # Bound the historical scan so Operations stays responsive on large DBs;
    # full order/cashier records remain authoritative and accessible.
    old_standalone_candidates = list(
        _workspace_order_queryset()
        .filter(visit__isnull=True, status=Order.Status.SERVED, created_at__lt=day_start)
        .order_by('-created_at', '-pk')[:501]
    )
    historical_collection_scan_limited = len(old_standalone_candidates) > 500
    historical_to_collect = [
        order for order in old_standalone_candidates[:500]
        if order.remaining_syp > 0
    ]
    # An unclosed previous business day requires management reconciliation.
    # Never finalize it on GET or silently start a new day.
    previous_open_day = BusinessDay.objects.filter(
        status__in=[BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED],
        business_date__lt=current_business_date(),
    ).order_by('-business_date').first()

    capabilities = get_staff_capabilities(request.user)
    primary_actions, more_actions = _daily_workspace_actions(request.user, capabilities)
    attention = get_staff_attention(request.user, capabilities)

    guest_policy = get_guest_wifi_policy()
    guest_pin_enabled = bool(guest_policy.enabled and guest_policy.require_venue_code)
    board_task = BusinessDayTask.objects.filter(
        business_day__business_date=current_business_date(),
        fingerprint="wifi:board-pin",
    ).first() if guest_pin_enabled else None

    response = render(
        request,
        "staff/home.html",
        {
            "visit_rows": live_visit_rows[:20],
            "stale_visit_rows": stale_visit_rows[:40],
            "active_orders": active_orders,
            "standalone_to_collect": standalone_to_collect[:10],
            "historical_to_collect": historical_to_collect[:10],
            "historical_collection_scan_limited": historical_collection_scan_limited,
            "previous_open_business_day": previous_open_day,
            "staff_attention": attention,
            "workspace_stats": {
                "open_visits": len(live_visit_rows),
                "stale_visits": len(stale_visit_rows),
                "active_orders": active_orders_count,
                "more_active_orders": active_orders_count > len(active_orders),
                "standalone_to_collect": len(standalone_to_collect),
                "historical_to_collect": len(historical_to_collect),
                "more_historical_to_collect": len(historical_to_collect) > 10 or historical_collection_scan_limited,
                "carryover_orders": carryover_order_count,
                "today_active_orders": today_active_count,
                "today_ready_orders": today_ready_count,
                "ready_orders": ready_count,
                "unpaid_orders": unpaid_count,
            },
            "workspace_caps": capabilities,
            "workspace_primary_actions": primary_actions,
            "workspace_more_actions": more_actions,
            "workspace_business_day_start": day_start,
            "guest_wifi_pin": current_venue_code(guest_policy) if guest_pin_enabled else "",
            "guest_wifi_board_task": board_task,
            # Used by the inline new-account form. Routine staff should not have
            # to leave Operations simply to create a customer account.
            "workspace_tables": TableArea.objects.select_related("room").order_by("room__name_ar", "name_ar"),
            "workspace_members": Member.objects.order_by("name_ar")[:200],
        },
    )
    # PIN visible to staff only: never cache the operations response.
    response['Cache-Control'] = 'private, no-store, max-age=0'
    return response
