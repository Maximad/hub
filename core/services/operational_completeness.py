"""Read-only operational completeness signals grounded in actual staff actions.

This is NOT certification of legacy stock, invoice provenance or accounting.
A trusted quantity requires evidence from the authorized physical-count flow;
a historic zero or estimated unit cost does not establish inventory correctness.
"""
from __future__ import annotations

from core.models import ActivityLog, Product


COUNT_AUDIT_ACTION = "inventory_stock_count_confirmed"


def counted_inventory_ids(item_ids):
    """Return materials with an explicitly confirmed physical count.

    Audit rows are written for both changed and unchanged physical counts.
    Older unchanged counts (before this release) had no durable evidence and
    deliberately remain unverified rather than being inferred from balances.
    """
    ids = {int(value) for value in item_ids}
    if not ids:
        return set()
    evidence = ActivityLog.objects.filter(
        action=COUNT_AUDIT_ACTION,
        details__item_id__in=ids,
    ).values_list("details", flat=True)
    return {
        item_id
        for row in evidence
        for item_id in [row.get("item_id")]
        if isinstance(item_id, int) and item_id in ids
    }


def inventory_completeness(item, *, counted):
    identity = bool(item.name_ar.strip() and item.code and item.unit)
    needs = []
    if not identity:
        needs.append("مراجعة الاسم والرمز ووحدة القياس")
    if not counted:
        needs.append("جرد فعلي موثق")
    if item.estimated_unit_cost_syp is None:
        needs.append("إدخال مرجع تكلفة عند استلام شراء فعلي")
    # A reference cost alone is never called verified: that is a later audit.
    if identity and counted:
        label, state = "مكتملة تشغيلياً", "operational_ready"
    elif counted:
        label, state = "تحتاج استكمالاً", "needs_completion"
    else:
        label, state = "غير مجرودة", "unreviewed"
    return {
        "state": state,
        "label": label,
        "needs": needs,
        "count_verified": counted,
        "has_reference_cost": item.estimated_unit_cost_syp is not None,
        "financially_verified": False,
    }


def recipe_completeness(product):
    # Intentionally independent of whether this product may be sold.
    if (
        product.product_type in {
            Product.ProductType.INTERNET,
            Product.ProductType.MEMBERSHIP,
            Product.ProductType.SERVICE,
        }
        and not product.requires_preparation
    ):
        return {"state": "not_applicable", "label": "لا تتطلب وصفة", "needs": []}
    rows = list(product.recipe_items.all())
    if not rows:
        return {
            "state": "unreviewed",
            "label": "غير موثّقة",
            "needs": ["تحديد مكوّنات الوصفة"],
        }
    active = [line for line in rows if line.is_active]
    if not active:
        return {
            "state": "needs_completion",
            "label": "مسودة تحتاج اعتماداً",
            "needs": ["مراجعة المقادير والمكوّنات قبل التفعيل"],
        }
    errors = [
        line for line in active
        if (not line.inventory_item_id
            or line.quantity_per_unit <= 0
            or line.unit != line.inventory_item.unit)
    ]
    if errors:
        return {
            "state": "needs_completion",
            "label": "تحتاج تصحيح وحدات",
            "needs": ["مراجعة وحدات ومقادير المكوّنات النشطة"],
        }
    return {
        "state": "operational_ready",
        "label": "وصفة مفعّلة",
        "needs": ["لم يُنجز التدقيق النهائي للتكلفة والمصدر"],
    }


def product_completeness(product, recipe=None):
    recipe = recipe if recipe is not None else recipe_completeness(product)
    has_price = product.price_syp > 0
    visible = product.visible_on_qr and product.orderable_on_qr
    ready = bool(product.is_available and has_price and visible)
    needs = []
    if not has_price:
        needs.append("اعتماد سعر بيع")
    if not ready:
        needs.append("مراجعة الإتاحة والظهور في قائمة البيع")
    if recipe["state"] not in ("operational_ready", "not_applicable"):
        needs.append("استكمال الوصفة بصورة مستقلة عن السماح بالبيع")
    return {
        "state": "operational_ready" if ready else "needs_completion",
        "label": "جاهز للبيع" if ready else "بحاجة لمراجعة البيع",
        "needs": needs,
        "recipe_state": recipe["state"],
        "financially_verified": False,
    }
