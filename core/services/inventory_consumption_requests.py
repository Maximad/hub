"""Prepare and cancel unposted physical internal-use requests.

D10 remains BLOCKED. Never call StockMovement.apply_to_stock, cash posting,
journal posting, recipe deduction or the old disabled manual movement handler.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import can_approve_partial_payment, user_has_capability
from core.models import (
    ActivityLog, AuditEvent, InventoryConsumptionRequest, InventoryItem,
    OrderConsumption,
)
from core.services.inventory_unit_entry import to_base_quantity


def allowed_categories(actor):
    if not actor or not actor.is_authenticated or not actor.is_active:
        return ()
    if not user_has_capability(actor, 'inventory'):
        return ()
    choices = [InventoryConsumptionRequest.Category.STAFF]
    if can_approve_partial_payment(actor) or actor.is_superuser:
        choices.extend((
            InventoryConsumptionRequest.Category.HUB_HOSPITALITY,
            InventoryConsumptionRequest.Category.EVENT,
        ))
    if actor.is_superuser:
        choices.append(InventoryConsumptionRequest.Category.OWNER_PERSONAL)
    return tuple(choices)


def request_inventory_consumption(*, actor, item_id, quantity, unit, category,
                                  reason, request_key, beneficiary_note='',
                                  event_label='', owner_treatment=''):
    if category not in allowed_categories(actor):
        raise PermissionDenied('لا تملك صلاحية إعداد هذا النوع من الاستهلاك.')
    try:
        key = uuid.UUID(str(request_key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('رمز الطلب غير صالح. حدّث الصفحة وأعد المحاولة.') from exc
    reason = (reason or '').strip()
    beneficiary_note = (beneficiary_note or '').strip()
    event_label = (event_label or '').strip()
    if not reason or len(reason) > 255:
        raise ValidationError('أدخل سبباً واضحاً لا يتجاوز 255 حرفاً.')
    if len(beneficiary_note) > 120 or len(event_label) > 120:
        raise ValidationError('الملاحظة أو اسم الفعالية أطول من الحد المسموح.')
    if category == InventoryConsumptionRequest.Category.OWNER_PERSONAL:
        if owner_treatment not in OrderConsumption.OwnerTreatment.values:
            raise ValidationError('حدد نية تسوية الاستخدام الشخصي للمالك.')
    elif owner_treatment:
        raise ValidationError('لا تُستخدم خيارات حساب المالك لهذا النوع.')
    if category == InventoryConsumptionRequest.Category.EVENT:
        if not event_label:
            raise ValidationError('أدخل اسم الفعالية.')
    elif event_label:
        raise ValidationError('اسم الفعالية يخص طلبات الفعاليات فقط.')
    try:
        entered = Decimal(str(quantity))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValidationError('أدخل كمية صالحة.') from exc
    if (not entered.is_finite() or entered <= 0 or
            entered >= Decimal('1000000000') or
            entered != entered.quantize(Decimal('0.000001'))):
        raise ValidationError('الكمية يجب أن تكون موجبة وبدقة لا تتجاوز ست منازل.')
    with transaction.atomic():
        existing = InventoryConsumptionRequest.objects.filter(request_key=key).first()
        if existing:
            if (existing.requested_by_id != actor.pk or
                existing.inventory_item_id != int(item_id) or
                existing.category != category or
                existing.entered_quantity != entered or
                existing.entered_unit != unit or
                existing.reason != reason or
                existing.owner_treatment != owner_treatment or
                existing.event_label != event_label or
                existing.beneficiary_note != beneficiary_note):
                raise ValidationError('تم استخدام رمز الطلب مع بيانات مختلفة؛ حدّث الصفحة.')
            return existing, False
        item = InventoryItem.objects.select_for_update().filter(pk=item_id, is_active=True).first()
        if not item:
            raise ValidationError('مادة المخزون غير متاحة.')
        canonical_qty = to_base_quantity(str(entered), item.unit, unit)
        # Validate against today's stock, but do not reserve or decrement it.
        if canonical_qty > item.current_quantity:
            raise ValidationError('الكمية المطلوبة تتجاوز الرصيد الحالي. لم تُخصم أي كمية.')
        unit_cost = item.estimated_unit_cost_syp
        cost_total = ((canonical_qty * unit_cost).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                      if unit_cost is not None else None)
        record, created = InventoryConsumptionRequest.objects.get_or_create(
            request_key=key,
            defaults={
                'inventory_item': item, 'category': category,
                'business_date': timezone.localdate(),
                'entered_quantity': entered, 'entered_unit': unit,
                'base_quantity': canonical_qty, 'base_unit': item.unit,
                'stock_quantity_at_request': item.current_quantity,
                'unit_cost_estimate_syp': unit_cost, 'total_cost_estimate_syp': cost_total,
                'reason': reason, 'beneficiary_note': beneficiary_note,
                'event_label': event_label, 'owner_treatment': owner_treatment,
                'requested_by': actor,
            },
        )
        if not created:
            if record.requested_by_id != actor.pk or record.inventory_item_id != item.pk:
                raise ValidationError('رمز الطلب مستخدم في سجل آخر.')
            return record, False
        record.full_clean()
        AuditEvent.objects.create(
            source=record, actor=actor, action='inventory_consumption_request_created',
            after_snapshot={
                'item_id': item.pk, 'category': category,
                'base_quantity': str(canonical_qty), 'base_unit': item.unit,
                'estimated_cost_syp': str(cost_total) if cost_total is not None else None,
                'state': record.state, 'stock_effect': False, 'accounting_effect': False,
                'policy': 'D10 BLOCKED',
            }, request_key=f'inventory-use-request:{key}', channel='staff-inventory',
        )
        ActivityLog.objects.create(actor=actor, action='inventory_consumption_request_created',
                                   details={'request_id': record.pk, 'policy': 'D10 BLOCKED'})
        return record, True


def cancel_consumption_request(*, actor, request_id, reason):
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('ليس لديك صلاحية إلغاء هذا الطلب.')
    reason = (reason or '').strip()
    if not reason or len(reason) > 255:
        raise ValidationError('أدخل سبب إلغاء موجزاً.')
    with transaction.atomic():
        record = InventoryConsumptionRequest.objects.select_for_update().get(pk=request_id)
        if not (actor.is_superuser or (
            user_has_capability(actor, 'inventory') and record.requested_by_id == actor.pk
        )):
            raise PermissionDenied('يمكن لمن أنشأ الطلب أو المالك إلغاءه فقط.')
        if record.state == record.State.CANCELLED:
            return record, False
        if record.state != record.State.PENDING:
            raise ValidationError('لا يمكن إلغاء طلب في حالة غير معروفة.')
        record.state = record.State.CANCELLED
        record.cancelled_by = actor
        record.cancelled_at = timezone.now()
        record.cancellation_reason = reason
        record.full_clean()
        record.save(update_fields=['state', 'cancelled_by', 'cancelled_at',
                                   'cancellation_reason', 'updated_at'])
        AuditEvent.objects.create(
            source=record, actor=actor, action='inventory_consumption_request_cancelled',
            before_snapshot={'state': record.State.PENDING},
            after_snapshot={'state': record.state, 'reason': reason,
                            'stock_effect': False, 'accounting_effect': False},
            request_key=f'inventory-use-cancel:{record.pk}', channel='staff-inventory',
        )
        ActivityLog.objects.create(actor=actor, action='inventory_consumption_request_cancelled',
                                   details={'request_id': record.pk, 'reason': reason})
        return record, True
