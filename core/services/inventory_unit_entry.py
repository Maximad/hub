"""Safe staff-facing quantity conversions without changing inventory base units.

Only physical mass and volume are convertible automatically. Pieces, packs and
containers have product-specific sizes; do not guess their conversion factors.
"""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError

FACTORS = {
    'g': {'g': Decimal('1'), 'kg': Decimal('1000')},
    'kg': {'kg': Decimal('1'), 'g': Decimal('0.001')},
    'ml': {'ml': Decimal('1'), 'liter': Decimal('1000')},
    'liter': {'liter': Decimal('1'), 'ml': Decimal('0.001')},
}
QUANTITY_STEP = Decimal('0.001')
COST_STEP = Decimal('0.01')


def allowed_entry_units(base_unit):
    return tuple(FACTORS.get(base_unit, {base_unit: Decimal('1')}))


def unit_multiplier(base_unit, entry_unit=None):
    entry = entry_unit or base_unit
    multiplier = FACTORS.get(base_unit, {base_unit: Decimal('1')}).get(entry)
    if multiplier is None:
        raise ValidationError('وحدة القياس المختارة غير متوافقة مع وحدة المادة. لا يمكن تحويل العبوات أو القطع تلقائياً.')
    return multiplier


def _decimal(raw, message):
    if raw is None or str(raw).strip() == '':
        raise ValidationError(message)
    try:
        result = Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(message) from exc
    if not result.is_finite():
        raise ValidationError(message)
    return result


def to_base_quantity(raw, base_unit, entry_unit=None, *, allow_zero=False):
    original = _decimal(raw, 'أدخل كمية رقمية صحيحة.')
    if original < 0 or (original == 0 and not allow_zero):
        raise ValidationError('الكمية يجب أن تكون موجبة، أو صفراً عند الجرد أو الاستلام.')
    base = original * unit_multiplier(base_unit, entry_unit)
    if base >= Decimal('1000000000') or base != base.quantize(QUANTITY_STEP):
        raise ValidationError('الكمية بعد التحويل تتجاوز حدود المخزون أو دقة ثلاث منازل عشرية.')
    return base.quantize(QUANTITY_STEP)


def to_base_unit_cost(raw, base_unit, entry_unit=None):
    """Convert entered price per selected unit into existing price per base unit.

    The schema stores only two fractional SYP digits. Reject fractional-cent
    base costs instead of rounding away money or distorting supplier totals.
    """
    entered = _decimal(raw, 'أدخل كلفة صحيحة للوحدة المختارة.')
    if entered < 0:
        raise ValidationError('الكلفة لا يمكن أن تكون سالبة.')
    base_cost = entered / unit_multiplier(base_unit, entry_unit)
    if base_cost >= Decimal('10000000000') or base_cost != base_cost.quantize(COST_STEP):
        raise ValidationError(
            'كلفة الوحدة بعد التحويل تحتاج دقة مالية أعلى من خانتين. '
            'أدخل الكمية وكلفتها بوحدة المادة الأساسية أو عدّل طريقة تسجيل الفاتورة.'
        )
    return base_cost.quantize(COST_STEP)


def receipt_quantities_from_post(request, purchase):
    """Return canonical receipt quantities; existing posting service enforces remaining."""
    fields = {name: raw for name, raw in request.POST.items()
              if name.startswith('quantity_') and not name.startswith('quantity_unit_')}
    if not fields:
        return None  # Original no-quantity behavior: receive all remaining.
    lines = {str(item.pk): item for item in
             purchase.items.select_related('inventory_item').all()}
    converted = {}
    for field, raw in fields.items():
        line_id = field[len('quantity_'):]
        if not line_id.isdigit() or line_id not in lines:
            raise ValidationError('يوجد بند استلام غير معروف.')
        line = lines[line_id]
        unit = request.POST.get('quantity_unit_' + line_id) or line.unit
        converted[line.pk] = to_base_quantity(raw, line.unit, unit, allow_zero=True)
    return converted
