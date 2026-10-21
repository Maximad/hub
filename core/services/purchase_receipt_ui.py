"""Read-only purchase receipt progress shared by Inventory and Finance UIs.

Do not derive the remaining receivable quantity from the purchase's financial
status: a partly received delivery may have a paid or partially paid liability.
"""
from dataclasses import replace
from decimal import Decimal
import logging
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db.models import Sum

from core.models import Purchase, PurchaseReceiptLine, InventoryItem
from core.services.inventory_unit_entry import allowed_entry_units, unit_multiplier


logger = logging.getLogger(__name__)


def _receipt_token(data):
    raw = data.get('receipt_token', '') if data is not None else ''
    try:
        return str(UUID(raw)) if len(raw) <= 36 else None
    except (ValueError, TypeError, AttributeError):
        return None


def receipt_posting_context(request, purchase, context):
    """One rendered form is one delivery, including retries across both UIs.

    Keep old callers without a form token on their existing idempotency path.
    Scope new tokens to actor and purchase, since dispatch keys are global.
    """
    if 'receipt_token' not in request.POST:
        return context
    token = _receipt_token(request.POST)
    if token is None:
        raise ValidationError('مرجع محاولة الاستلام غير صالح. راجع الكميات ثم أعد التأكيد.')
    return replace(context, idempotency_key=f'purchase-receipt:{purchase.pk}:{request.user.pk}:{token}')


def receipt_failure_messages(error, purchase):
    if isinstance(error, ValidationError):
        return list(error.messages)
    logger.exception('Purchase receipt failed for purchase %s', purchase.pk)
    return ['تعذر تأكيد الاستلام الآن. أعد المحاولة بالقيم نفسها. إذا تكرر الخطأ، أبلغ المسؤول برقم الشراء.']


def purchase_receipt_ui(purchase, *, data=None, errors=None):
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
        entry_unit = data.get(f'quantity_unit_{item.pk}', item.unit) if data is not None else item.unit
        units = allowed_entry_units(item.unit)
        valid_unit = entry_unit in units
        rows.append({
            "item": item,
            "ordered": item.quantity,
            "received": received,
            "remaining": remaining,
            "entered_quantity": data.get(f'quantity_{item.pk}', '0') if data is not None else remaining,
            "entered_unit": entry_unit,
            "invalid_entry_unit": not valid_unit,
            "entry_max": remaining / unit_multiplier(item.unit, entry_unit) if valid_unit else remaining,
            "entry_units": [(code, label) for code, label in InventoryItem.Unit.choices
                            if code in allowed_entry_units(item.unit)],
        })
    can_receive = (
        purchase.status != Purchase.Status.CANCELLED
        and any(row["remaining"] > 0 for row in rows)
    )
    return {
        "receipt_rows": rows, "can_receive_purchase": can_receive,
        "receipt_token": _receipt_token(data) or str(uuid4()),
        "receipt_errors": errors or [],
    }
