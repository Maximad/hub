"""Opt-in, bounded follow-up from *current* operations, not paper history.

One durable case per (source, identity, issue), and one projection per open
Business Day. This service observes catalog and count evidence only: it never
posts stock, sales, costs, purchases, cash or notifications.
"""
from __future__ import annotations

from datetime import timedelta

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog, InternetSession, InventoryItem, Order, OrderItem, Product, Purchase, PurchaseReceipt, PurchaseReceiptLine, StockMovement, OperationsImportReceipt
from internet.models import InternetSessionNetworkOperation
from internet.session_network_backends import PROVISION_ERROR
from core.services.operational_completeness import counted_inventory_ids, recipe_completeness
from .duty import current_duty_users
from .models import BusinessDay, BusinessDayTask, OperationalReviewCase
from .tasks import AUTO_WAIVE_NOTE


LIVE_STATUSES = {BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED}
ISSUE_TITLES = {
    OperationalReviewCase.IssueCode.NEEDS_COUNT: "جرد فعلي",
    OperationalReviewCase.IssueCode.RECIPE_REVIEW: "مراجعة وصفة",
    OperationalReviewCase.IssueCode.PRICE_REVIEW: "اعتماد سعر",
    OperationalReviewCase.IssueCode.NETWORK_REVIEW: "متابعة اتصال الإنترنت",
    OperationalReviewCase.IssueCode.RECEIPT_REVIEW: "مراجعة مستند استلام شراء",
}
SOURCE_CAPABILITIES = {
    OperationalReviewCase.IssueCode.NEEDS_COUNT: "inventory_stock_count",
    OperationalReviewCase.IssueCode.RECIPE_REVIEW: "inventory",
    OperationalReviewCase.IssueCode.PRICE_REVIEW: "inventory",
    OperationalReviewCase.IssueCode.NETWORK_REVIEW: "internet_billing",
    OperationalReviewCase.IssueCode.RECEIPT_REVIEW: "inventory",
}

RECHECK_SALT = "hub.operational-review-recheck.v1"
REVIEW_GUIDANCE = {
    OperationalReviewCase.IssueCode.NEEDS_COUNT: (
        "المادة تحتاج إلى جرد فعلي موثق.",
        "أكّد الكمية الموجودة فعلياً من صفحة الجرد، حتى لو طابقت الرصيد المسجّل.",
    ),
    OperationalReviewCase.IssueCode.RECIPE_REVIEW: (
        "وصفة المنتج تحتاج إلى استكمال أو مراجعة مقاديرها ووحداتها.",
        "احفظ مكوّناً نشطاً واحداً على الأقل بمقادير موجبة ووحدات تطابق مواد المخزون.",
    ),
    OperationalReviewCase.IssueCode.PRICE_REVIEW: (
        "سعر بيع المنتج غير محدد أو يساوي صفراً.",
        "احفظ سعر بيع أكبر من صفر من أدوات القائمة. هذا لا يعتبر تدقيقاً نهائياً للسعر أو التكلفة.",
    ),
    OperationalReviewCase.IssueCode.NETWORK_REVIEW: (
        "سُجّل فشل متكرر في تفعيل اتصال هذه الجلسة.",
        "راجع الجلسة الأصلية. تزول المتابعة بعد زوال فشل التفعيل أو إنهاء الجلسة من مسارها المعتاد.",
    ),
    OperationalReviewCase.IssueCode.RECEIPT_REVIEW: (
        "استلام الشراء يحتاج مراجعة المورد أو التكلفة أو حركة الاستلام.",
        "راجع المورد وحركات الاستلام الأصلية. تصحيح استلام مثبت يحتاج المسؤول المالي، ولا يكفي تعديل كلفة بند قديم.",
    ),
}


def review_presentation(case, user):
    """Links follow the destination's permission, independently of visibility."""
    reason, completion = REVIEW_GUIDANCE.get(case.issue_code, ("تحتاج مراجعة.", "راجع السجل الأصلي."))
    routes = {
        OperationalReviewCase.IssueCode.NEEDS_COUNT: ("inventory_stock_count", "staff_inventory_stock_count"),
        OperationalReviewCase.IssueCode.RECIPE_REVIEW: ("inventory", "staff_recipe_edit"),
        OperationalReviewCase.IssueCode.PRICE_REVIEW: ("staff_home", "staff_menu_tools"),
        OperationalReviewCase.IssueCode.NETWORK_REVIEW: ("members/internet", "staff_internet_session"),
        OperationalReviewCase.IssueCode.RECEIPT_REVIEW: ("inventory", "staff_inventory_purchase_detail"),
    }
    capability, route = routes.get(case.issue_code, (None, None))
    url = ""
    if route and user_has_capability(user, capability):
        if case.issue_code == OperationalReviewCase.IssueCode.NEEDS_COUNT:
            url = reverse(route) + f"?item={case.source_id}"
        elif case.issue_code == OperationalReviewCase.IssueCode.PRICE_REVIEW:
            url = reverse(route) + f"?product={case.source_id}"
        else:
            url = reverse(route, args=[case.source_id])
    return {"reason": reason, "completion": completion, "url": url}


def can_recheck_case(day, case, actor):
    if not (
        getattr(actor, "is_authenticated", False) and actor.is_active
        and getattr(actor, "role", "") != "internet_provider"
        and user_has_capability(actor, "staff_home")
        and user_has_capability(actor, SOURCE_CAPABILITIES.get(case.issue_code))
    ):
        return False
    if actor.is_superuser or getattr(actor, "role", "") in {"admin", "cashier"}:
        return True
    holder = case_duty_holder(day, case)
    return holder is not None and holder.pk == actor.pk


def _recheck_source(case, *, lock=False):
    models = {
        OperationalReviewCase.SourceType.INVENTORY: InventoryItem,
        OperationalReviewCase.SourceType.PRODUCT: Product,
        OperationalReviewCase.SourceType.PURCHASE: Purchase,
        OperationalReviewCase.SourceType.INTERNET_SESSION: InternetSession,
    }
    model = models.get(case.source_type)
    expected_types = {
        OperationalReviewCase.IssueCode.NEEDS_COUNT: OperationalReviewCase.SourceType.INVENTORY,
        OperationalReviewCase.IssueCode.RECIPE_REVIEW: OperationalReviewCase.SourceType.PRODUCT,
        OperationalReviewCase.IssueCode.PRICE_REVIEW: OperationalReviewCase.SourceType.PRODUCT,
        OperationalReviewCase.IssueCode.NETWORK_REVIEW: OperationalReviewCase.SourceType.INTERNET_SESSION,
        OperationalReviewCase.IssueCode.RECEIPT_REVIEW: OperationalReviewCase.SourceType.PURCHASE,
    }
    if model is None or expected_types.get(case.issue_code) != case.source_type:
        raise ValidationError("نوع السجل الأصلي غير معروف. اطلب مراجعة الإدارة.")
    rows = model.objects.all()
    if lock:
        rows = rows.select_for_update()
    source = rows.filter(pk=case.source_id).first()
    if source is None:
        raise ValidationError("السجل الأصلي غير موجود. اطلب مراجعة الإدارة، ولم تُغلق المتابعة.")
    return source


def _recheck_status(day, case, actor, *, lock=False):
    if day.status not in LIVE_STATUSES:
        raise ValidationError("التحقق من المتابعة يتطلب يوم تشغيل مفتوحاً.")
    if not can_recheck_case(day, case, actor):
        raise PermissionDenied
    source = _recheck_source(case, lock=lock)
    return (
        OperationalReviewCase.Status.OPEN if case.issue_code in case_spec(case.source_type, source)
        else OperationalReviewCase.Status.RESOLVED
    )


def preview_case_recheck(day, case, *, actor):
    """Read current evidence for one existing case. Never discover new cases."""
    desired = _recheck_status(day, case, actor)
    return {
        "case": case,
        "desired": desired,
        "changed": case.status != desired,
        **review_presentation(case, actor),
        "token": signing.dumps({
            "actor": actor.pk, "day": day.pk, "case": case.pk, "desired": desired,
        }, salt=RECHECK_SALT),
    }


@transaction.atomic
def confirm_case_recheck(day, *, actor, token):
    try:
        payload = signing.loads(token, salt=RECHECK_SALT, max_age=600)
    except (signing.BadSignature, TypeError) as exc:
        raise ValidationError("انتهت صلاحية المعاينة أو لم تعد صالحة. أعد التحقق من المتابعة.") from exc
    if not isinstance(payload, dict) or payload.get("actor") != actor.pk or payload.get("day") != day.pk:
        raise PermissionDenied
    # Use the same day lock as duty handover and closing, then serialize case
    # confirmation. A duplicate submission therefore has no additional effect.
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    case = OperationalReviewCase.objects.select_for_update().filter(pk=payload.get("case")).first()
    if case is None:
        raise ValidationError("المتابعة لم تعد موجودة. حدّث الصفحة.")
    desired = _recheck_status(day, case, actor, lock=True)
    if desired != payload.get("desired"):
        raise ValidationError("تغيّر السجل الأصلي منذ المعاينة. أعد التحقق قبل التأكيد.")
    changed = case.status != desired
    if changed:
        previous = case.status
        case.status = desired
        case.resolved_at = timezone.now() if desired == OperationalReviewCase.Status.RESOLVED else None
        case.save(update_fields=["status", "resolved_at", "reviewed_at"])
        _project(day, case)
        ActivityLog.objects.create(
            actor=actor, action="operational.review_rechecked",
            details={"business_day_id": day.pk, "case_id": case.pk, "before": previous, "after": desired},
        )
    return {"changed": changed, "status": desired}


def _candidate_ids(*, product_ids, inventory_ids, internet_ids=None, purchase_ids=None, include_recent, limit):
    if not 1 <= limit <= 30:
        raise ValidationError("حد المراجعات يجب أن يكون بين 1 و30.")
    given_products = {int(pk) for pk in (product_ids or [])}
    given_inventory = {int(pk) for pk in (inventory_ids or [])}
    given_internet = {int(pk) for pk in (internet_ids or [])}
    given_purchases = {int(pk) for pk in (purchase_ids or [])}
    if any(pk < 1 for pk in given_products | given_inventory | given_internet | given_purchases):
        raise ValidationError("معرّفات المواد والمنتجات يجب أن تكون موجبة.")
    if len(given_products) + len(given_inventory) + len(given_internet) + len(given_purchases) > limit:
        raise ValidationError("اختر مجموعة أصغر، بما لا يتجاوز حد المراجعات.")
    if include_recent:
        since = timezone.now() - timedelta(days=14)
        # A recently *entered* line is not proof of a recent real sale:
        # old notebooks are still being keyed in today, sometimes without
        # an import_key marker. Only the existing transactional staff POS
        # creation receipt is strong enough for automatic discovery.
        # The short candidate window also bounds work on large imports.
        recent_lines = list(
            OrderItem.objects.filter(created_at__gte=since)
            .exclude(order__status=Order.Status.CANCELLED)
            .exclude(prep_status=OrderItem.PrepStatus.CANCELLED)
            .exclude(item_note__contains="import_key=")
            .order_by("-created_at", "-pk")
            .values_list("order__public_code", "product_id")[:400]
        )
        order_codes = {str(code) for code, _ in recent_lines}
        certified_order_codes = set(
            ActivityLog.objects.filter(
                action="staff_pos_order_created",
                actor__isnull=False,
                created_at__gte=since,
                details__order_public_code__in=order_codes,
            ).values_list("details__order_public_code", flat=True)
        ) if order_codes else set()
        operational_orders = [
            product_id for code, product_id in recent_lines
            if str(code) in certified_order_codes
        ]
        receipts = (
            StockMovement.objects.filter(
                created_at__gte=since,
                movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,
                is_cancelled=False,
            )
            .values_list("inventory_item_id", flat=True).distinct().order_by("inventory_item_id")
        )
        slots = limit - len(given_products) - len(given_inventory) - len(given_internet) - len(given_purchases)
        for pk in operational_orders:
            if len(given_products) + len(given_inventory) + len(given_internet) + len(given_purchases) >= limit:
                break
            if pk not in given_products:
                given_products.add(pk)
        slots = limit - len(given_products) - len(given_inventory) - len(given_internet) - len(given_purchases)
        if slots:
            for pk in receipts[:slots]:
                if pk not in given_inventory:
                    given_inventory.add(pk)
    if include_recent:
        # Only durable recent *actual* failed provision operations. No imports,
        # stale legacy sessions or raw error strings enter the follow-up.
        since = timezone.now() - timedelta(days=14)
        slots = limit - len(given_products) - len(given_inventory) - len(given_internet) - len(given_purchases)
        if slots:
            failures = (
                InternetSessionNetworkOperation.objects.filter(
                    operation=InternetSessionNetworkOperation.Operation.PROVISION,
                    status=InternetSessionNetworkOperation.Status.FAILED,
                    attempt_count__gte=3,
                    session__status=InternetSession.Status.ACTIVE,
                    session__network_status=PROVISION_ERROR,
                    created_at__gte=since,
                )
                .values_list("session_id", flat=True).distinct().order_by("session_id")
            )
            for pk in failures[:slots]:
                given_internet.add(pk)
    # Purchase candidates are deliberately explicit only: a recent creation
    # date alone cannot distinguish a real receipt from reconstructed notebooks.
    return given_products, given_inventory, given_internet, given_purchases


def _review_source_label(kind, source):
    if kind == OperationalReviewCase.SourceType.INTERNET_SESSION:
        return f"جلسة إنترنت #{source.pk}"
    if kind == OperationalReviewCase.SourceType.PURCHASE:
        return f"شراء مستلم #{source.pk}"
    return source.name_ar


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
    if kind == OperationalReviewCase.SourceType.PURCHASE:
        # Only an actual unreversed receipt is eligible. A notebook-imported
        # purchase must not enter the current-operations review queue.
        if OperationsImportReceipt.objects.filter(purchase=source).exists():
            return set()
        if not PurchaseReceipt.objects.filter(
            purchase=source, reversed_at__isnull=True, lines__isnull=False,
        ).exists():
            return set()
        missing_supplier = not (source.vendor_id or (source.supplier_name or '').strip())
        # Check the posted receiving movement, not a mutable PurchaseItem price:
        # editing an old item cannot silently certify an already posted receipt.
        zero_receipt_cost = StockMovement.objects.filter(
            purchase_receipt_line__receipt__purchase=source,
            movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,
            purchase_receipt_line__receipt__reversed_at__isnull=True,
            is_cancelled=False,
        ).filter(Q(unit_cost_syp__isnull=True) | Q(unit_cost_syp__lte=0)).exists()
        missing_movement = PurchaseReceiptLine.objects.filter(
            receipt__purchase=source, receipt__reversed_at__isnull=True,
            stock_movement__isnull=True,
        ).exists()
        return {OperationalReviewCase.IssueCode.RECEIPT_REVIEW} if (
            missing_supplier or zero_receipt_cost or missing_movement
        ) else set()
    if kind == OperationalReviewCase.SourceType.INTERNET_SESSION:
        # Network provisioning failure is supported by the durable outbox,
        # not by a guessed session age, billing status, or text in operator
        # notes. Three actual failed attempts avoid transient retry noise.
        if (
            source.status == InternetSession.Status.ACTIVE
            and source.network_provider != InternetSession.NetworkProvider.MANUAL
            and source.network_status == PROVISION_ERROR
            and InternetSessionNetworkOperation.objects.filter(
                session=source,
                operation=InternetSessionNetworkOperation.Operation.PROVISION,
                status=InternetSessionNetworkOperation.Status.FAILED,
                attempt_count__gte=3,
            ).exists()
        ):
            return {OperationalReviewCase.IssueCode.NETWORK_REVIEW}
        return set()
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
    day, *, actor=None, product_ids=None, inventory_ids=None, internet_ids=None, purchase_ids=None,
    include_recent=True, limit=20, dry_run=True,
):
    """Review at most 30 NEW sources plus existing open cases, atomically.

    No scans of historic notebook aggregates. This operation is not called by
    business_day_tick, order save, staff login or deployment.
    """
    if day.status not in LIVE_STATUSES:
        raise ValidationError("حالات المتابعة تتطلب يوم تشغيل مفتوح.")
    ids_product, ids_inventory, ids_internet, ids_purchase = _candidate_ids(
        product_ids=product_ids, inventory_ids=inventory_ids, internet_ids=internet_ids,
        purchase_ids=purchase_ids,
        include_recent=include_recent, limit=limit,
    )
    objects = {}
    for obj in Product.objects.filter(pk__in=ids_product).prefetch_related("recipe_items__inventory_item"):
        objects[(OperationalReviewCase.SourceType.PRODUCT, obj.pk)] = obj
    for obj in InventoryItem.objects.filter(pk__in=ids_inventory):
        objects[(OperationalReviewCase.SourceType.INVENTORY, obj.pk)] = obj
    for obj in InternetSession.objects.filter(pk__in=ids_internet):
        objects[(OperationalReviewCase.SourceType.INTERNET_SESSION, obj.pk)] = obj
    for obj in Purchase.objects.filter(pk__in=ids_purchase):
        objects[(OperationalReviewCase.SourceType.PURCHASE, obj.pk)] = obj
    if (len(ids_product) + len(ids_inventory) + len(ids_internet) + len(ids_purchase)) != len(objects):
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
        extra_internet = InternetSession.objects.filter(
            pk__in=[pk for kind, pk in missing if kind == OperationalReviewCase.SourceType.INTERNET_SESSION]
        )
        extra_purchases = Purchase.objects.filter(
            pk__in=[pk for kind, pk in missing if kind == OperationalReviewCase.SourceType.PURCHASE]
        )
        for obj in extra_products:
            objects[(OperationalReviewCase.SourceType.PRODUCT, obj.pk)] = obj
        for obj in extra_inventory:
            objects[(OperationalReviewCase.SourceType.INVENTORY, obj.pk)] = obj
        for obj in extra_internet:
            objects[(OperationalReviewCase.SourceType.INTERNET_SESSION, obj.pk)] = obj
        for obj in extra_purchases:
            objects[(OperationalReviewCase.SourceType.PURCHASE, obj.pk)] = obj

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
                    source_label=_review_source_label(kind, source),
                    evidence={"source": "recent_actual_operation_or_selected_record"},
                )
                existing[(kind, pk, issue)] = case
                counts["created"] += 1
                changes.append(f"created {kind}:{pk}:{issue}")
            elif case.status != desired:
                case.status = desired
                case.resolved_at = timezone.now() if desired == OperationalReviewCase.Status.RESOLVED else None
                case.source_label = _review_source_label(kind, source)
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
        # Match individual confirmation's day -> case lock order. Inserting a
        # task projection can otherwise wait for a day FK lock while another
        # transaction holds that day and waits for the same review case.
        day = BusinessDay.objects.select_for_update().get(pk=day.pk)
        result = sync_operational_review_cases(day, **kwargs)
        if dry_run:
            transaction.set_rollback(True)
    return result
