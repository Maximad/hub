"""Read-only quantity context from existing count audits and movement records.

Movement creation is not proof of application or actual consumption. Do not
sum this history into a new stock ledger or silently certify current balances.
"""
from decimal import Decimal, InvalidOperation

from core.models import AuditEvent, InventoryItem, StockMovement


COUNT_ACTIONS = ('inventory_physical_count_verified', 'inventory_physical_count_confirmed')


def physical_count_audits(item_ids):
    return AuditEvent.objects.filter(
        channel='staff-stock-count', action__in=COUNT_ACTIONS,
        before_snapshot__item_id__in=list(item_ids),
    )


def _snapshot_quantity(raw):
    try:
        if len(str(raw)) > 32:
            return None
        quantity = Decimal(str(raw))
        return quantity if quantity.is_finite() and 0 <= quantity < Decimal('1000000000') else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def quantity_provenance(items):
    """Two batch queries, at most one count and one movement row per item.

    Use audit creation time, never notebook business dates or item.updated_at.
    Keep the newest incomplete audit visible instead of falling back to an
    older convenient quantity. No actor details, free-form notes or costs.
    """
    items = list(items)
    if not items:
        return {}
    ids = [item.pk for item in items]
    audits = physical_count_audits(ids).order_by(
        'before_snapshot__item_id', '-created_at', '-pk',
    ).distinct('before_snapshot__item_id').values(
        'pk', 'created_at', 'before_snapshot', 'after_snapshot',
    )
    counts = {row['before_snapshot']['item_id']: row for row in audits
              if type(row['before_snapshot'].get('item_id')) is int}
    movements = {
        row['inventory_item_id']: row
        for row in StockMovement.objects.filter(inventory_item_id__in=ids)
        .order_by('inventory_item_id', '-created_at', '-pk').distinct('inventory_item_id')
        .values('pk', 'inventory_item_id', 'created_at', 'movement_type',
                'quantity', 'unit', 'is_cancelled')
    }
    units = dict(InventoryItem.Unit.choices)
    types = dict(StockMovement.MovementType.choices)
    result = {}
    for item in items:
        audit = counts.get(item.pk)
        movement = movements.get(item.pk)
        after = audit['after_snapshot'] if audit and isinstance(audit['after_snapshot'], dict) else {}
        quantity = _snapshot_quantity(after.get('quantity'))
        unit = audit['before_snapshot'].get('unit') if audit else None
        if not isinstance(unit, str):
            unit = None
        later = bool(audit and movement and movement['created_at'] >= audit['created_at'])
        same_unit = unit == item.unit
        matches = quantity is not None and same_unit and quantity == item.current_quantity
        if not audit:
            state, label = 'no_snapshot', 'لا توجد كمية جرد موثقة'
        elif quantity is None or unit not in units:
            state, label = 'incomplete_snapshot', 'تفاصيل الجرد تحتاج مراجعة'
        elif not same_unit:
            state, label = 'unit_changed', 'تغيّرت وحدة المخزون بعد الجرد'
        elif later:
            state, label = 'later_movements', 'حركات مسجّلة بعد الجرد'
        elif not matches:
            state, label = 'quantity_differs', 'الرصيد لا يطابق آخر جرد'
        else:
            state, label = 'matches_count', 'الرصيد يطابق آخر جرد'
        result[item.pk] = {
            'state': state, 'label': label,
            'count_id': audit['pk'] if audit else None,
            'counted_at': audit['created_at'] if audit else None,
            'counted_quantity': quantity,
            'counted_unit': units.get(unit, 'وحدة غير موثقة'),
            'matches_count': matches, 'has_later_movements': later,
            'latest_movement': ({
                'created_at': movement['created_at'],
                'label': types.get(movement['movement_type'], 'حركة مخزون'),
                'is_cancelled': movement['is_cancelled'],
            } if later else None),
        }
    return result
