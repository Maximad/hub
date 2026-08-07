from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum
from django.utils import timezone

from core.models import (ActivityLog, FinancialAccount, InventoryItem, PostingBatch,
                         PostingEntry, Purchase, PurchasePayment, PurchaseReceipt,
                         PurchaseReceiptLine, PurchaseReturn, PurchaseReturnLine,
                         StockMovement)
from .engine import dispatch, lock_accounts
from .exceptions import InvalidTransition


def _account(code, account_type):
    """Resolve an explicitly configured account; never invent an accounting choice.

    Account creation and activation are deployment/finance-owner decisions.  Posting
    must stop when either has not happened, rather than silently creating a live
    account (the previous behaviour made an ambiguous chart-of-accounts decision).
    """
    try:
        account = FinancialAccount.objects.get(code=code)
    except FinancialAccount.DoesNotExist as exc:
        raise InvalidTransition(
            f'الحساب {code} غير معرّف؛ يجب أن يعتمده مسؤول المالية قبل الترحيل.'
        ) from exc
    if not account.is_active:
        raise InvalidTransition(
            f'الحساب {code} غير فعّال؛ الترحيل موقوف حتى اعتماد مسؤول المالية.'
        )
    if account.account_type != account_type:
        raise InvalidTransition(
            f'نوع الحساب {code} لا يطابق قاعدة الترحيل المعتمدة.'
        )
    return account


def _batch(source, context, operation, debit_account, credit_account, amount, reversal_of=None):
    batch = PostingBatch.objects.create(
        operation_type=operation, source_content_type=ContentType.objects.get_for_model(source),
        source_object_id=str(source.pk), business_date=context.date_for(source),
        status=PostingBatch.Status.POSTED, idempotency_key=f'{context.idempotency_key}:{operation}',
        actor=context.actor, approver=context.approver, posted_at=timezone.now(),
        reversal_of=reversal_of, channel=context.channel)
    PostingEntry.objects.bulk_create([
        PostingEntry(batch=batch, account=debit_account, debit=amount),
        PostingEntry(batch=batch, account=credit_account, credit=amount),
    ])
    return batch


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
        value = Decimal('0')
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
            movement.full_clean(); movement.save(); movement.apply_to_stock(); value += line_value
        if not receipt.lines.exists():
            raise InvalidTransition('يجب إدخال كمية استلام موجبة.')
        # Recognition policy: supplier liability is recognized as goods are received.
        _batch(receipt, context, 'purchase.receipt.liability',
               _account('inventory:purchases', FinancialAccount.AccountType.ASSET),
               _account('payable:suppliers', FinancialAccount.AccountType.LIABILITY), value)
        source.received_by=context.actor; source.received_at=timezone.now(); source.save(update_fields=['received_by','received_at','updated_at'])
        sync_state(source)
        ActivityLog.objects.create(actor=context.actor, action='purchase_received', details={'purchase_id': source.pk, 'receipt_id': receipt.pk})
        return receipt
    return dispatch('purchase.receive', purchase, context, handle)


def pay(purchase, context, amount, source_account, payment_method=''):
    amount = Decimal(str(amount))
    def handle(source):
        if amount <= 0 or amount > source.remaining_syp:
            raise InvalidTransition('مبلغ دفع غير صحيح.')
        if source_account.account_type not in {FinancialAccount.AccountType.ASSET, FinancialAccount.AccountType.CLEARING}:
            raise InvalidTransition('يجب الدفع من حساب أصل أو مقاصة.')
        batch = _batch(source, context, 'purchase.payment', _account('payable:suppliers', FinancialAccount.AccountType.LIABILITY), source_account, amount)
        payment = PurchasePayment.objects.create(purchase=source, amount_syp=amount, source_account=source_account,
            actor=context.actor, approver=context.approver, business_date=context.date_for(source),
            idempotency_key=context.idempotency_key, posting_batch=batch)
        source.payment_method=payment_method or source.payment_method; source.save(update_fields=['payment_method','updated_at'])
        sync_state(source); return payment
    return dispatch('purchase.pay', purchase, context, handle)


def cancel(purchase, context, reason):
    def handle(source):
        if not reason.strip(): raise InvalidTransition('سبب الإلغاء مطلوب.')
        lines = list(PurchaseReceiptLine.objects.filter(receipt__purchase=source, receipt__reversed_at__isnull=True).select_related('purchase_item__inventory_item','receipt'))
        needed = {}
        for line in lines: needed[line.purchase_item.inventory_item_id] = needed.get(line.purchase_item.inventory_item_id, 0) + line.received_quantity
        locked = {i.pk:i for i in InventoryItem.objects.select_for_update().filter(pk__in=needed)}
        if any(locked[pk].current_quantity < qty for pk, qty in needed.items()):
            raise InvalidTransition('لا يمكن الإلغاء: تم استهلاك مخزون مستلم؛ يلزم تصحيح مخوّل.')
        ret = PurchaseReturn.objects.create(purchase=source, business_date=context.date_for(source), actor=context.actor,
                                             idempotency_key=context.idempotency_key, reason=reason)
        for line in lines:
            rl=PurchaseReturnLine.objects.create(purchase_return=ret, receipt_line=line, purchase_item=line.purchase_item, returned_quantity=line.received_quantity)
            mv=StockMovement(inventory_item=line.purchase_item.inventory_item,business_date=ret.business_date,movement_type=StockMovement.MovementType.RETURN_TO_VENDOR,direction=StockMovement.Direction.OUT,quantity=line.received_quantity,unit=line.purchase_item.unit,unit_cost_syp=line.purchase_item.unit_cost_syp,total_value_syp=(line.received_quantity*line.purchase_item.unit_cost_syp).quantize(Decimal('0.01')),related_purchase=source,related_purchase_item=line.purchase_item,purchase_return_line=rl,reason=reason,created_by=context.actor)
            mv.full_clean(); mv.save(); mv.apply_to_stock(); line.receipt.reversed_at=timezone.now(); line.receipt.reversed_by=context.actor; line.receipt.reversal_reason=reason; line.receipt.save()
        for receipt in source.receipts.filter(reversed_at__isnull=False):
            original = PostingBatch.objects.filter(source_content_type=ContentType.objects.get_for_model(receipt), source_object_id=str(receipt.pk), operation_type='purchase.receipt.liability', status=PostingBatch.Status.POSTED).first()
            if original and not original.reversals.exists():
                value = sum((line.received_quantity * line.purchase_item.unit_cost_syp for line in receipt.lines.select_related('purchase_item')), Decimal('0')).quantize(Decimal('0.01'))
                _batch(receipt, context, f'purchase.receipt.reverse.{receipt.pk}',
                       _account('payable:suppliers', FinancialAccount.AccountType.LIABILITY),
                       _account('inventory:purchases', FinancialAccount.AccountType.ASSET), value, original)
        for payment in source.payments.filter(reversed_at__isnull=True).select_related('posting_batch','source_account'):
            payment.reversal_batch=_batch(payment, context, f'purchase.payment.reverse.{payment.pk}', payment.source_account, _account('payable:suppliers', FinancialAccount.AccountType.LIABILITY), payment.amount_syp, payment.posting_batch)
            payment.reversed_at=timezone.now(); payment.save(update_fields=['reversal_batch','reversed_at','updated_at'])
        source.cancelled_at=timezone.now(); source.cancellation_reason=reason; source.save(update_fields=['cancelled_at','cancellation_reason','updated_at']); sync_state(source); return source
    return dispatch('purchase.cancel', purchase, context, handle)


return_purchase = cancel
reverse = cancel
