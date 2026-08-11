from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import (ActivityLog, FinanceReviewItem, InventoryItem, Purchase, PurchaseReceipt,
                         PurchaseReceiptLine, PurchaseReturn, PurchaseReturnLine,
                         StockMovement)
from .engine import dispatch, lock_accounts
from .exceptions import InvalidTransition


def sync_state(purchase):
    received = PurchaseReceiptLine.objects.filter(receipt__purchase=purchase, receipt__reversed_at__isnull=True).aggregate(v=Sum('received_quantity'))['v'] or 0
    returned = PurchaseReturnLine.objects.filter(purchase_return__purchase=purchase, purchase_return__reversed_at__isnull=True).aggregate(v=Sum('returned_quantity'))['v'] or 0
    paid = purchase.amount_paid_syp
    if purchase.cancelled_at:
        status = Purchase.Status.CANCELLED
    elif paid >= purchase.total_syp and purchase.total_syp:
        status = Purchase.Status.PAID
    elif paid:
        status = Purchase.Status.PARTIALLY_PAID
    elif received > returned:
        status = Purchase.Status.RECEIVED
    else:
        status = Purchase.Status.DRAFT
    Purchase.objects.filter(pk=purchase.pk).update(status=status)
    purchase.status = status
    return status


def receive(purchase, context, quantities=None):
    """Receive requested quantities by purchase-item id; omitted means all remaining."""
    def handle(source):
        if source.status == Purchase.Status.CANCELLED:
            raise InvalidTransition('لا يمكن استلام شراء ملغى.')
        items = list(source.items.select_for_update().select_related('inventory_item').order_by('pk'))
        if not items:
            raise InvalidTransition('لا يمكن استلام شراء بلا بنود.')
        lock_accounts(InventoryItem.objects.filter(pk__in=[item.inventory_item_id for item in items]))
        receipt = PurchaseReceipt.objects.create(purchase=source, business_date=context.date_for(source),
                                                  actor=context.actor, idempotency_key=context.idempotency_key)
        for item in items:
            prior = item.receipt_lines.filter(receipt__reversed_at__isnull=True).aggregate(v=Sum('received_quantity'))['v'] or 0
            remaining = item.quantity - prior
            qty = Decimal(str(quantities.get(item.pk, 0))) if quantities is not None else remaining
            if qty < 0 or qty > remaining:
                raise InvalidTransition(f'كمية استلام البند {item.pk} تتجاوز المتبقي.')
            if not qty:
                continue
            line = PurchaseReceiptLine.objects.create(receipt=receipt, purchase_item=item, received_quantity=qty)
            line_value = (qty * item.unit_cost_syp).quantize(Decimal('0.01'))
            movement = StockMovement(inventory_item=item.inventory_item, business_date=receipt.business_date,
                movement_type=StockMovement.MovementType.PURCHASE_RECEIVED, direction=StockMovement.Direction.IN,
                quantity=qty, unit=item.unit, unit_cost_syp=item.unit_cost_syp, total_value_syp=line_value,
                related_purchase=source, related_purchase_item=item, purchase_receipt_line=line,
                reason='استلام شراء', created_by=context.actor, approved_by=context.approver)
            movement.full_clean(); movement.save(); movement.apply_to_stock()
        if not receipt.lines.exists():
            raise InvalidTransition('يجب إدخال كمية استلام موجبة.')
        # D07-D11 are deliberately unconfirmed. Receipt is operational only;
        # queue finance review rather than guessing a payable or clearing policy.
        FinanceReviewItem.objects.update_or_create(
            issue_code='purchase_finance_policy_unconfirmed',
            record_type=source._meta.label, record_id=str(source.pk),
            defaults={
                'reason': 'استلام شراء تشغيلي فقط؛ الترحيل المالي محظور حتى اعتماد D07–D11.',
                'details': {'purchase_id': source.pk, 'receipt_id': receipt.pk,
                            'decision_ids': ['D07', 'D08', 'D09', 'D11']},
                'resolved_at': None,
            },
        )
        source.received_by=context.actor; source.received_at=timezone.now(); source.save(update_fields=['received_by','received_at','updated_at'])
        sync_state(source)
        ActivityLog.objects.create(actor=context.actor, action='purchase_received', details={'purchase_id': source.pk, 'receipt_id': receipt.pk})
        return receipt
    return dispatch('purchase.receive', purchase, context, handle)


def pay(purchase, context, amount, source_account, payment_method=''):
    raise InvalidTransition(
        'دفع المورد غير مرحّل مالياً: قرارات D07–D11 غير معتمدة. سجّل الشراء كتشغيلي فقط للمراجعة المالية.'
    )


def cancel(purchase, context, reason):
    def handle(source):
        if not reason.strip(): raise InvalidTransition('سبب الإلغاء مطلوب.')
        lines = list(PurchaseReceiptLine.objects.filter(receipt__purchase=source, receipt__reversed_at__isnull=True).select_related('purchase_item__inventory_item','receipt'))
        needed = {}
        for line in lines: needed[line.purchase_item.inventory_item_id] = needed.get(line.purchase_item.inventory_item_id, 0) + line.received_quantity
        locked = {i.pk:i for i in InventoryItem.objects.select_for_update().filter(pk__in=needed)}
        if any(locked[pk].current_quantity < qty for pk, qty in needed.items()):
            raise InvalidTransition('لا يمكن الإلغاء: تم استهلاك مخزون مستلم؛ يلزم تصحيح مخوّل.')
        ret = None
        if lines:
            ret = PurchaseReturn.objects.create(purchase=source, business_date=context.date_for(source), actor=context.actor,
                                                 idempotency_key=context.idempotency_key, reason=reason)
        for line in lines:
            rl=PurchaseReturnLine.objects.create(purchase_return=ret, receipt_line=line, purchase_item=line.purchase_item, returned_quantity=line.received_quantity)
            mv=StockMovement(inventory_item=line.purchase_item.inventory_item,business_date=ret.business_date,movement_type=StockMovement.MovementType.RETURN_TO_VENDOR,direction=StockMovement.Direction.OUT,quantity=line.received_quantity,unit=line.purchase_item.unit,unit_cost_syp=line.purchase_item.unit_cost_syp,total_value_syp=(line.received_quantity*line.purchase_item.unit_cost_syp).quantize(Decimal('0.01')),related_purchase=source,related_purchase_item=line.purchase_item,purchase_return_line=rl,reason=reason,created_by=context.actor)
            mv.full_clean(); mv.save(); mv.apply_to_stock(); line.receipt.reversed_at=timezone.now(); line.receipt.reversed_by=context.actor; line.receipt.reversal_reason=reason; line.receipt.save()
        source.cancelled_at=timezone.now(); source.cancellation_reason=reason; source.save(update_fields=['cancelled_at','cancellation_reason','updated_at']); sync_state(source); return source
    return dispatch('purchase.cancel', purchase, context, handle)


return_purchase = cancel
reverse = cancel
