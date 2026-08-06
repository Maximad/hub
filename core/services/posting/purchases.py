from django.utils import timezone
from django.db.models import Sum
from core.models import ActivityLog, InventoryItem, Purchase, StockMovement
from .engine import dispatch, lock_accounts
from .exceptions import InvalidTransition


def receive(purchase, context):
    def handle(source):
        if source.status == Purchase.Status.CANCELLED: raise InvalidTransition('لا يمكن استلام شراء ملغى.')
        # Lock receipt lines as well as the purchase. This makes the quantity check
        # authoritative even if another worker attempts receipt concurrently.
        items=list(source.items.select_for_update().select_related('inventory_item').order_by('pk'))
        if not items: raise InvalidTransition('لا يمكن استلام شراء بلا بنود.')
        lock_accounts(InventoryItem.objects.filter(pk__in=[x.inventory_item_id for x in items]))
        for item in items:
            received = StockMovement.objects.filter(
                related_purchase_item=item,
                movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,
                is_cancelled=False,
            ).aggregate(total=Sum('quantity'))['total'] or 0
            if received:
                if received != item.quantity:
                    raise InvalidTransition(f'كمية الاستلام للبند {item.pk} لا تطابق كمية الشراء.')
                continue
            if item.quantity <= 0:
                raise InvalidTransition(f'كمية بند الشراء {item.pk} يجب أن تكون موجبة.')
            movement=StockMovement(inventory_item=item.inventory_item,business_date=source.business_date,movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,direction=StockMovement.Direction.IN,quantity=item.quantity,unit=item.unit,unit_cost_syp=item.unit_cost_syp,total_value_syp=item.line_total_syp,related_purchase=source,related_purchase_item=item,reason='استلام شراء',created_by=context.actor,approved_by=context.approver)
            movement.full_clean(); movement.save(); movement.apply_to_stock()
        source.received_by=context.actor; source.received_at=timezone.now()
        source.status=Purchase.Status.PAID if source.remaining_syp == 0 and source.amount_paid_syp else (Purchase.Status.PARTIALLY_PAID if source.amount_paid_syp else Purchase.Status.RECEIVED)
        source.save(); ActivityLog.objects.create(actor=context.actor,action='purchase_received',details={'purchase_id':source.pk,'posting_key':context.idempotency_key}); return source
    return dispatch('purchase.receive', purchase, context, handle)


def pay(purchase, context, amount, payment_method, paid_from):
    def handle(source):
        if amount <= 0 or amount > source.remaining_syp: raise InvalidTransition('مبلغ دفع غير صحيح.')
        source.amount_paid_syp += amount; source.payment_method=payment_method; source.paid_from=paid_from
        source.status=Purchase.Status.PAID if source.remaining_syp == 0 else Purchase.Status.PARTIALLY_PAID; source.save(); return source
    return dispatch('purchase.pay', purchase, context, handle)


def return_purchase(purchase, context, reason):
    return cancel(purchase, context, reason)


def cancel(purchase, context, reason):
    def handle(source):
        if not reason.strip(): raise InvalidTransition('سبب الإلغاء مطلوب.')
        movements=list(source.stock_movements.filter(direction=StockMovement.Direction.IN,is_cancelled=False).select_related('inventory_item'))
        lock_accounts(InventoryItem.objects.filter(pk__in=[m.inventory_item_id for m in movements]))
        for old in movements:
            movement=StockMovement(inventory_item=old.inventory_item,business_date=source.business_date,movement_type=StockMovement.MovementType.RETURN_TO_VENDOR,direction=StockMovement.Direction.OUT,quantity=old.quantity,unit=old.unit,unit_cost_syp=old.unit_cost_syp,total_value_syp=old.total_value_syp,related_purchase=source,reason='عكس شراء: '+reason,created_by=context.actor)
            movement.full_clean(); movement.save(); movement.apply_to_stock(); old.is_cancelled=True; old.cancellation_reason=reason; old.save()
        source.status=Purchase.Status.CANCELLED; source.cancellation_reason=reason; source.cancelled_at=timezone.now(); source.save(); return source
    return dispatch('purchase.cancel', purchase, context, handle)


def reverse(purchase, context, reason): return cancel(purchase, context, reason)


def adjust_stock(movement, context):
    def handle(source):
        item=InventoryItem.objects.select_for_update().get(pk=source.inventory_item_id)
        source.inventory_item=item; source.created_by=source.created_by or context.actor; source.approved_by=context.approver
        source.full_clean(); source.save(); source.apply_to_stock()
        ActivityLog.objects.create(actor=context.actor,action='stock_adjustment_posted',details={'stock_movement_id':source.pk,'posting_key':context.idempotency_key})
        return source
    return dispatch('inventory.adjust',movement,context,handle)
