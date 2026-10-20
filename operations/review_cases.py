"""Opt-in, bounded follow-up from *current* operations, not paper history.

One durable case per (source, identity, issue), and one projection per open
Business Day. This service observes catalog and count evidence only: it never
posts stock, sales, costs, purchases, cash or notifications.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog, InventoryItem, Order, OrderItem, Product, StockMovement
from core.services.operational_completeness import counted_inventory_ids, recipe_completeness
from .duty import current_duty_users
from .models import BusinessDay, BusinessDayTask, OperationalReviewCase
from .tasks import AUTO_WAIVE_NOTE


LIVE_STATUSES = {BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED}
ISSUE_TITLES = {
    OperationalReviewCase.IssueCode.NEEDS_COUNT: "جرد فعلي",
    OperationalReviewCase.IssueCode.RECIPE_REVIEW: "مراجعة وصفة",
    OperationalReviewCase.IssueCode.PRICE_REVIEW: "اعتماد سعر",
}
SOURCE_CAPABILITIES = {
    OperationalReviewCase.IssueCode.NEEDS_COUNT: "inventory_stock_count",
    OperationalReviewCase.IssueCode.RECIPE_REVIEW: "inventory",
    OperationalReviewCase.IssueCode.PRICE_REVIEW: "inventory",
}


def _candidate_ids(*, product_ids, inventory_ids, include_recent, limit):
    if not 1 <= limit <= 30:
        raise ValidationError("حد المراجعات يجب أن يكون بين 1 و30.")
    given_products = {int(pk) for pk in (product_ids or [])}
    given_inventory = {int(pk) for pk in (inventory_ids or [])}
    if any(pk < 1 for pk in given_products | given_inventory):
        raise ValidationError("معرّفات المواد والمنتجات يجب أن تكون موجبة.")
    if len(given_products) + len(given_inventory) > limit:
        raise ValidationError("اختر مجموعة أصغر، بما لا يتجاوز حد المراجعات.")
    if include_recent:
        since = timezone.now() - timedelta(days=14)
        operational_orders = (
            OrderItem.objects.filter(
                created_at__gte=since,
            )
            .exclude(order__status=Order.Status.CANCELLED)
            .exclude(prep_status=OrderItem.PrepStatus.CANCELLED)
            .exclude(item_note__contains="import_key=")
            .values_list("product_id", flat=True).distinct().order_by("product_id")
        )
        receipts = (
            StockMovement.objects.filter(
                created_at__gte=since,
                movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,
                is_cancelled=False,
            )
            .values_list("inventory_item_id", flat=True).distinct().order_by("inventory_item_id")
        )
        slots = limit - len(given_products) - len(given_inventory)
        for pk in operational_orders[:slots]:
            if pk not in given_products:
                given_products.add(pk)
        slots = limit - len(given_products) - len(given_inventory)
        if slots:
            for pk in receipts[:slots]:
                if pk not in given_inventory:
                    given_inventory.add(pk)
    return given_products, given_inventory


def case_spec(kind, source):
    """Map a source to its *current* unresolved conditions."""
    if kind == OperationalReviewCase.SourceType.INVENTORY:
        verified = source.pk in counted_inventory_ids([source.pk])
        return {
            OperationalReviewCase.IssueCode.NEEDS_COUNT
        } if source.is_active and not verified else set()
    if kind == OperationalReviewCase.SourceType.PRODUCT:
        issues = set()
        if source.price_syp <= 0:
            issues.add(OperationalReviewCase.IssueCode.PRICE_REVIEW)
        if source.requires_preparation and recipe_completeness(source)["state"] != "operational_ready":
            issues.add(OperationalReviewCase.IssueCode.RECIPE_REVIEW)
        return issues
    return set()


def case_duty_holder(day, case):
    """Duty check happens dynamically. Pending handover never transfers work."""
    capability = SOURCE_CAPABILITIES.get(case.issue_code)
    for user in current_duty_users(day, "operations"):
        if user_has_capability(user, capability):
            return user
    return None


def _project(day, case):
    """Daily projection without due time, auto reminder or artificial assignee."""
    key = f"review:{case.pk}"
    defaults = {
        "kind": BusinessDayTask.Kind.MANUAL,
        "title_ar": f"{ISSUE_TITLES[case.issue_code]}: {case.source_label}",
        "details": "حالة متابعة مستمرة. افتح السجل الأصلي، ثم حدّث المراجعات للتحقق من اكتمالها.",
        "priority": "normal",
        "is_required": False,
        "source_type": "OperationalReviewCase",
        "source_id": str(case.pk),
        "due_at": None,
        "responsibility_role": "",
        "assigned_to": None,
        "metadata": {
            "review_case_id": case.pk,
            "issue_code": case.issue_code,
            "source_type": case.source_type,
            "source_id": case.source_id,
        },
    }
    if case.status == OperationalReviewCase.Status.RESOLVED:
        task = BusinessDayTask.objects.filter(business_day=day, fingerprint=key).first()
        if task is None:
            return False
        if task.status == BusinessDayTask.Status.PENDING:
            task.status = BusinessDayTask.Status.WAIVED
            task.completion_note = AUTO_WAIVE_NOTE
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completion_note", "completed_at", "updated_at"])
        return False
    task, created = BusinessDayTask.objects.get_or_create(
        business_day=day, fingerprint=key, defaults=defaults,
    )
    if task.status == BusinessDayTask.Status.WAIVED and task.completion_note == AUTO_WAIVE_NOTE:
        task.status = BusinessDayTask.Status.PENDING
        task.completion_note = ""
        task.completed_at = None
        task.completed_by = None
        task.save(update_fields=["status", "completion_note", "completed_at", "completed_by", "updated_at"])
    return created


def sync_operational_review_cases(
    day, *, actor=None, product_ids=None, inventory_ids=None,
    include_recent=True, limit=20, dry_run=True,
):
    """Review at most 30 NEW sources plus existing open cases, atomically.

    No scans of historic notebook aggregates. This operation is not called by
    business_day_tick, order save, staff login or deployment.
    """
    if day.status not in LIVE_STATUSES:
        raise ValidationError("حالات المتابعة تتطلب يوم تشغيل مفتوح.")
    ids_product, ids_inventory = _candidate_ids(
        product_ids=product_ids, inventory_ids=inventory_ids,
        include_recent=include_recent, limit=limit,
    )
    objects = {}
    for obj in Product.objects.filter(pk__in=ids_product).prefetch_related("recipe_items__inventory_item"):
        objects[(OperationalReviewCase.SourceType.PRODUCT, obj.pk)] = obj
    for obj in InventoryItem.objects.filter(pk__in=ids_inventory):
        objects[(OperationalReviewCase.SourceType.INVENTORY, obj.pk)] = obj
    if (len(ids_product) + len(ids_inventory)) != len(objects):
        raise ValidationError("هناك منتج أو مادة غير موجودة، لم ننشئ أي مراجعة.")

    # Re-evaluate only cases already tracked, rather than scanning all stock.
    tracked = list(OperationalReviewCase.objects.select_for_update().all())
    tracked_keys = {(c.source_type, c.source_id) for c in tracked}
    missing = tracked_keys - set(objects)
    if missing:
        extra_products = Product.objects.filter(
            pk__in=[pk for kind, pk in missing if kind == OperationalReviewCase.SourceType.PRODUCT]
        ).prefetch_related("recipe_items__inventory_item")
        extra_inventory = InventoryItem.objects.filter(
            pk__in=[pk for kind, pk in missing if kind == OperationalReviewCase.SourceType.INVENTORY]
        )
        for obj in extra_products:
            objects[(OperationalReviewCase.SourceType.PRODUCT, obj.pk)] = obj
        for obj in extra_inventory:
            objects[(OperationalReviewCase.SourceType.INVENTORY, obj.pk)] = obj

    counts = {"created": 0, "resolved": 0, "reopened": 0, "unchanged": 0, "task_created": 0}
    changes = []
    existing = {(c.source_type, c.source_id, c.issue_code): c for c in tracked}
    for (kind, pk), source in sorted(objects.items()):
        issues = case_spec(kind, source)
        existing_issues = {
            case.issue_code for case in tracked if case.source_type == kind and case.source_id == pk
        }
        for issue in sorted(issues | existing_issues):
            desired = (
                OperationalReviewCase.Status.OPEN if issue in issues
                else OperationalReviewCase.Status.RESOLVED
            )
            case = existing.get((kind, pk, issue))
            if case is None and desired == OperationalReviewCase.Status.RESOLVED:
                continue
            if case is None:
                case = OperationalReviewCase.objects.create(
                    source_type=kind, source_id=pk, issue_code=issue,
                    source_label=source.name_ar,
                    evidence={"source": "recent_actual_operation_or_selected_record"},
                )
                existing[(kind, pk, issue)] = case
                counts["created"] += 1
                changes.append(f"created {kind}:{pk}:{issue}")
            elif case.status != desired:
                case.status = desired
                case.resolved_at = timezone.now() if desired == OperationalReviewCase.Status.RESOLVED else None
                case.source_label = source.name_ar
                case.save(update_fields=["status", "resolved_at", "source_label", "reviewed_at"])
                action = "resolved" if desired == OperationalReviewCase.Status.RESOLVED else "reopened"
                counts[action] += 1
                changes.append(f"{action} {kind}:{pk}:{issue}")
            else:
                counts["unchanged"] += 1
            # Only current day projection: do not rewrite historical closed days.
            if _project(day, case):
                counts["task_created"] += 1
    if any(counts[name] for name in ("created", "resolved", "reopened")):
        ActivityLog.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            action="operational.review_sync",
            details={"business_day_id": day.pk, "counts": counts, "changes": changes[:30]},
        )
    return {"counts": counts, "changes": changes, "dry_run": dry_run}


def run_review_sync(day, **kwargs):
    dry_run = kwargs.get("dry_run", True)
    with transaction.atomic():
        result = sync_operational_review_cases(day, **kwargs)
        if dry_run:
            transaction.set_rollback(True)
    return result
