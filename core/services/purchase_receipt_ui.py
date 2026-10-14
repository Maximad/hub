"""Read-only purchase receipt progress shared by Inventory and Finance UIs.

Do not derive the remaining receivable quantity from the purchase's financial
status: a partly received delivery may have a paid or partially paid liability.
"""
from decimal import Decimal

from django.db.models import Sum

from core.models import Purchase, PurchaseReceiptLine


def purchase_receipt_ui(purchase):
    items = list(purchase.items.select_related("inventory_item").order_by("pk"))
    posted = (
        PurchaseReceiptLine.objects.filter(
            purchase_item_id__in=[item.pk for item in items],
            receipt__reversed_at__isnull=True,
        )
        .values("purchase_item_id")
        .annotate(received=Sum("received_quantity"))
    )
    received_by_id = {
        record["purchase_item_id"]: record["received"] or Decimal("0")
        for record in posted
    }
    rows = []
    for item in items:
        received = received_by_id.get(item.pk, Decimal("0"))
        remaining = max(item.quantity - received, Decimal("0"))
        rows.append({
            "item": item,
            "ordered": item.quantity,
            "received": received,
            "remaining": remaining,
        })
    can_receive = (
        purchase.status != Purchase.Status.CANCELLED
        and any(row["remaining"] > 0 for row in rows)
    )
    return {"receipt_rows": rows, "can_receive_purchase": can_receive}
