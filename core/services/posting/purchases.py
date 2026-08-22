from dataclasses import replace
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum
from django.utils import timezone

from accounts.permissions import is_owner_or_admin
from core.models import (
    ActivityLog, AuditEvent, CashMovement, FinanceReviewItem, FinancialAccount,
    InventoryItem, PostingBatch, PostingEntry, Purchase, PurchasePayment,
    PurchaseReceipt, PurchaseReceiptLine, PurchaseReturn, PurchaseReturnLine,
    StockMovement,
)
from .engine import dispatch, ensure_period_open, lock_accounts
from .exceptions import InvalidTransition
from .ledger import post_balanced_batch
from .policy import require_finance_actor
from .transfers import approval_limit


ACCOUNT_TYPES = {
    'inventory:purchases': FinancialAccount.AccountType.ASSET,
    'payable:suppliers': FinancialAccount.AccountType.LIABILITY,
    'cash:main': FinancialAccount.AccountType.ASSET,
    'bank:main': FinancialAccount.AccountType.ASSET,
    'payable:owner': FinancialAccount.AccountType.LIABILITY,
    'equity:owner_contribution': FinancialAccount.AccountType.EQUITY,
}
SETTLEMENT_CODES = {'cash:main', 'bank:main', 'payable:owner', 'equity:owner_contribution'}


def _account(code):
    account = FinancialAccount.objects.filter(code=code).first()
    if (not account or not account.is_active or account.account_type != ACCOUNT_TYPES[code]
            or (account.business_unit or '') != ''):
        raise InvalidTransition(f'الحساب المالي المعتمد {code} مفقود أو غير فعّال أو نوعه/نطاقه غير صالح.')
    return account


def _batch(source, context, operation, entries, *, reversal_of=None, reason=''):
    batch = PostingBatch.objects.create(
        operation_type=operation, source_content_type=ContentType.objects.get_for_model(source),
        source_object_id=str(source.pk), business_date=context.date_for(source),
        idempotency_key=f'{context.idempotency_key}:batch', actor=context.actor,
        approver=context.approver, reason=reason,
        channel=context.channel, metadata=dict(context.request_metadata),
    )
    PostingEntry.objects.bulk_create([
        PostingEntry(batch=batch, account=account, debit=amount if side == 'debit' else None,
                     credit=amount if side == 'credit' else None, description=description)
        for account, side, amount, description in entries
    ])
    batch = post_balanced_batch(batch)
    if reversal_of is not None:
        batch.reversal_of = reversal_of
        batch.full_clean()
        batch.save(update_fields=['reversal_of', 'updated_at'])
    return batch


def _audit(context, action, source, details, *, approver=None):
    AuditEvent.objects.create(
        actor=context.actor, approver=approver if approver is not None else context.approver,
        action=action, source_content_type=ContentType.objects.get_for_model(source),
        source_object_id=str(source.pk), request_key=context.idempotency_key,
        channel=context.channel, after_snapshot=details,
    )


def financial_state(purchase):
    """Authoritative D07/D09/D08 state, derived exclusively from posted artifacts."""
    receipt_ct = ContentType.objects.get_for_model(PurchaseReceipt)
    return_ct = ContentType.objects.get_for_model(PurchaseReturn)
    receipt_ids = purchase.receipts.values_list('pk', flat=True)
    return_ids = purchase.returns.values_list('pk', flat=True)
    recognized = PostingEntry.objects.filter(
        batch__status=PostingBatch.Status.POSTED,
        batch__operation_type='purchase.receipt.liability',
        batch__source_content_type=receipt_ct, batch__source_object_id__in=[str(x) for x in receipt_ids],
        account__code='payable:suppliers', credit__isnull=False,
    ).aggregate(v=Sum('credit'))['v'] or Decimal('0')
    reversed_value = PostingEntry.objects.filter(
        batch__status=PostingBatch.Status.POSTED,
        batch__operation_type='purchase.return.liability_reversal',
        batch__source_content_type=return_ct, batch__source_object_id__in=[str(x) for x in return_ids],
        account__code='payable:suppliers', debit__isnull=False,
    ).aggregate(v=Sum('debit'))['v'] or Decimal('0')
    paid = purchase.payments.filter(reversed_at__isnull=True, posting_batch__status=PostingBatch.Status.POSTED).aggregate(v=Sum('amount_syp'))['v'] or Decimal('0')
    liability = max(Decimal(recognized) - Decimal(reversed_value), Decimal('0'))
    return {'recognized': liability, 'received_recognized': Decimal(recognized),
            'financially_reversed': Decimal(reversed_value), 'paid': Decimal(paid),
            'outstanding': max(liability - Decimal(paid), Decimal('0'))}


def sync_state(purchase):
    received = PurchaseReceiptLine.objects.filter(receipt__purchase=purchase, receipt__reversed_at__isnull=True).aggregate(v=Sum('received_quantity'))['v'] or 0
    returned = PurchaseReturnLine.objects.filter(purchase_return__purchase=purchase, purchase_return__reversed_at__isnull=True).aggregate(v=Sum('returned_quantity'))['v'] or 0
    paid = financial_state(purchase)['paid']
    outstanding = financial_state(purchase)['outstanding']
    if purchase.cancelled_at: status = Purchase.Status.CANCELLED
    elif paid and not outstanding: status = Purchase.Status.PAID
    elif paid: status = Purchase.Status.PARTIALLY_PAID
    elif received > returned: status = Purchase.Status.RECEIVED
    else: status = Purchase.Status.DRAFT
    Purchase.objects.filter(pk=purchase.pk).update(status=status); purchase.status = status
    return status


def receive(purchase, context, quantities=None):
    def handle(source):
        if source.status == Purchase.Status.CANCELLED: raise InvalidTransition('لا يمكن استلام شراء ملغى.')
        items = list(source.items.select_for_update().select_related('inventory_item').order_by('pk'))
        if not items: raise InvalidTransition('لا يمكن استلام شراء بلا بنود.')
        inventory, payable = _account('inventory:purchases'), _account('payable:suppliers')
        ensure_period_open(context.date_for(source), inventory); ensure_period_open(context.date_for(source), payable)
        lock_accounts(InventoryItem.objects.filter(pk__in=[item.inventory_item_id for item in items]))
        receipt = PurchaseReceipt.objects.create(purchase=source, business_date=context.date_for(source), actor=context.actor, idempotency_key=context.idempotency_key)
        total = Decimal('0')
        for item in items:
            prior = item.receipt_lines.filter(receipt__reversed_at__isnull=True).aggregate(v=Sum('received_quantity'))['v'] or 0
            remaining = item.quantity - prior
            qty = Decimal(str(quantities.get(item.pk, 0))) if quantities is not None else remaining
            if qty < 0 or qty > remaining: raise InvalidTransition(f'كمية استلام البند {item.pk} تتجاوز المتبقي.')
            if not qty: continue
            line = PurchaseReceiptLine.objects.create(receipt=receipt, purchase_item=item, received_quantity=qty)
            value = (qty * item.unit_cost_syp).quantize(Decimal('0.01')); total += value
            movement = StockMovement(inventory_item=item.inventory_item, business_date=receipt.business_date,
                movement_type=StockMovement.MovementType.PURCHASE_RECEIVED, direction=StockMovement.Direction.IN,
                quantity=qty, unit=item.unit, unit_cost_syp=item.unit_cost_syp, total_value_syp=value,
                related_purchase=source, related_purchase_item=item, purchase_receipt_line=line,
                reason='استلام شراء', created_by=context.actor, approved_by=context.approver)
            movement.full_clean(); movement.save(); movement.apply_to_stock()
        if not receipt.lines.exists(): raise InvalidTransition('يجب إدخال كمية استلام موجبة.')
        batch = _batch(receipt, context, 'purchase.receipt.liability', [
            (inventory, 'debit', total, 'استلام مشتريات مخزون'),
            (payable, 'credit', total, 'إثبات التزام المورد'),
        ])
        source.received_by=context.actor; source.received_at=timezone.now(); source.save(update_fields=['received_by','received_at','updated_at'])
        sync_state(source)
        ActivityLog.objects.create(actor=context.actor, action='purchase_received', details={'purchase_id': source.pk, 'receipt_id': receipt.pk})
        _audit(context, 'purchase_liability_recognized', receipt, {'purchase_id': source.pk, 'business_date': str(receipt.business_date), 'amount_syp': str(total), 'debit': inventory.code, 'credit': payable.code, 'posting_batch_id': str(batch.pk)})
        return receipt
    return dispatch('purchase.receive', purchase, context, handle)


def _payment_approver(context, amount):
    require_finance_actor(context, 'دفع المورد')
    if amount >= approval_limit():
        approver = context.approver
        if (not approver or not approver.is_active or approver.pk == context.actor.pk
                or not (approver.is_superuser or getattr(approver, 'role', '') == 'admin' or is_owner_or_admin(approver))):
            raise InvalidTransition(f'دفعة المورد بقيمة {approval_limit():,.0f} ل.س أو أكثر تحتاج موافقة مدير نشط مختلف.')
        return approver
    return context.approver or context.actor


def pay(purchase, context, amount, source_account, payment_method=''):
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    approver = _payment_approver(context, amount)
    context = replace(context, approver=approver)
    def handle(source):
        if source.status == Purchase.Status.CANCELLED: raise InvalidTransition('لا يمكن دفع شراء ملغى.')
        if amount <= 0: raise InvalidTransition('مبلغ دفعة المورد يجب أن يكون موجباً.')
        if not source_account or source_account.code not in SETTLEMENT_CODES: raise InvalidTransition('حساب تسوية المورد غير معتمد.')
        settlement = _account(source_account.code); payable = _account('payable:suppliers')
        state = financial_state(source)
        if state['outstanding'] <= 0: raise InvalidTransition('لا يوجد التزام مورد معترف به وغير مسدد لهذا الشراء.')
        if amount > state['outstanding']: raise InvalidTransition('مبلغ الدفعة يتجاوز التزام المورد المستحق لهذا الشراء.')
        ensure_period_open(context.date_for(source), payable); ensure_period_open(context.date_for(source), settlement)
        batch = PostingBatch.objects.create(operation_type='purchase.payment', business_date=context.date_for(source),
            idempotency_key=f'{context.idempotency_key}:batch', actor=context.actor, approver=approver,
            channel=context.channel, metadata=dict(context.request_metadata))
        payment = PurchasePayment.objects.create(purchase=source, amount_syp=amount, source_account=settlement,
            actor=context.actor, approver=approver, business_date=context.date_for(source),
            idempotency_key=context.idempotency_key, posting_batch=batch)
        batch.source_content_type=ContentType.objects.get_for_model(payment); batch.source_object_id=str(payment.pk); batch.save(update_fields=['source_content_type','source_object_id','updated_at'])
        PostingEntry.objects.bulk_create([
            PostingEntry(batch=batch, account=payable, debit=amount, description='تسوية التزام المورد'),
            PostingEntry(batch=batch, account=settlement, credit=amount, description='مصدر تسوية المورد'),
        ]); batch=post_balanced_batch(batch)
        if settlement.code == 'cash:main':
            CashMovement.objects.create(business_date=payment.business_date, movement_type=CashMovement.MovementType.SUPPLIER_PAYMENT,
                direction=CashMovement.Direction.OUT, amount_syp=amount, vendor=source.vendor,
                financial_account=settlement, is_generated=True, title=f'دفعة مورد للشراء {source.pk}',
                created_by=context.actor, approved_by=approver)
        sync_state(source); ActivityLog.objects.create(actor=context.actor, action='purchase_payment_posted', details={'purchase_id':source.pk,'payment_id':payment.pk})
        _audit(context, 'supplier_payment_posted', payment, {'purchase_id':source.pk,'business_date':str(payment.business_date),'amount_syp':str(amount),'debit':payable.code,'credit':settlement.code,'posting_batch_id':str(batch.pk)}, approver=approver)
        return payment
    return dispatch('purchase.payment', purchase, context, handle)


def reverse_payment(payment, context, reason):
    def handle(source):
        require_finance_actor(context, 'عكس دفعة المورد')
        if source.reversed_at or source.reversal_batch_id: raise InvalidTransition('دفعة المورد معكوسة مسبقاً.')
        if not (reason or '').strip(): raise InvalidTransition('سبب عكس دفعة المورد مطلوب.')
        payable, settlement = _account('payable:suppliers'), _account(source.source_account.code)
        ensure_period_open(context.date_for(source), payable); ensure_period_open(context.date_for(source), settlement)
        batch = _batch(source, context, 'purchase.payment.reversal', [
            (settlement, 'debit', source.amount_syp, reason.strip()),
            (payable, 'credit', source.amount_syp, reason.strip()),
        ], reversal_of=source.posting_batch, reason=reason.strip())
        source.reversed_at=timezone.now(); source.reversal_batch=batch; source.save(update_fields=['reversed_at','reversal_batch','updated_at'])
        if settlement.code == 'cash:main':
            CashMovement.objects.create(business_date=context.date_for(source), movement_type=CashMovement.MovementType.SUPPLIER_PAYMENT,
                direction=CashMovement.Direction.IN, amount_syp=source.amount_syp, vendor=source.purchase.vendor,
                financial_account=settlement, is_generated=True, title=f'عكس دفعة مورد للشراء {source.purchase_id}', notes=reason.strip(), created_by=context.actor, approved_by=context.approver)
        sync_state(source.purchase); ActivityLog.objects.create(actor=context.actor, action='purchase_payment_reversed', details={'payment_id':source.pk,'reason':reason.strip()})
        _audit(context, 'supplier_payment_reversed', source, {'purchase_id':source.purchase_id,'business_date':str(context.date_for(source)),'amount_syp':str(source.amount_syp),'debit':settlement.code,'credit':payable.code,'reason':reason.strip(),'posting_batch_id':str(batch.pk)})
        return source
    return dispatch('purchase.payment.reverse', payment, context, handle)


def cancel(purchase, context, reason):
    def handle(source):
        if not reason.strip(): raise InvalidTransition('سبب الإلغاء مطلوب.')
        lines = list(PurchaseReceiptLine.objects.filter(receipt__purchase=source, receipt__reversed_at__isnull=True).select_related('purchase_item__inventory_item','receipt'))
        needed = {}
        for line in lines: needed[line.purchase_item.inventory_item_id] = needed.get(line.purchase_item.inventory_item_id, 0) + line.received_quantity
        locked = {i.pk:i for i in InventoryItem.objects.select_for_update().filter(pk__in=needed)}
        if any(locked[pk].current_quantity < qty for pk, qty in needed.items()): raise InvalidTransition('لا يمكن الإلغاء: تم استهلاك مخزون مستلم؛ يلزم تصحيح مخوّل.')
        ret = None
        outstanding = financial_state(source)['outstanding']; return_value = Decimal('0')
        if lines: ret = PurchaseReturn.objects.create(purchase=source, business_date=context.date_for(source), actor=context.actor, idempotency_key=context.idempotency_key, reason=reason)
        for line in lines:
            value=(line.received_quantity*line.purchase_item.unit_cost_syp).quantize(Decimal('0.01')); return_value += value
            rl=PurchaseReturnLine.objects.create(purchase_return=ret, receipt_line=line, purchase_item=line.purchase_item, returned_quantity=line.received_quantity)
            mv=StockMovement(inventory_item=line.purchase_item.inventory_item,business_date=ret.business_date,movement_type=StockMovement.MovementType.RETURN_TO_VENDOR,direction=StockMovement.Direction.OUT,quantity=line.received_quantity,unit=line.purchase_item.unit,unit_cost_syp=line.purchase_item.unit_cost_syp,total_value_syp=value,related_purchase=source,related_purchase_item=line.purchase_item,purchase_return_line=rl,reason=reason,created_by=context.actor)
            mv.full_clean(); mv.save(); mv.apply_to_stock(); line.receipt.reversed_at=timezone.now(); line.receipt.reversed_by=context.actor; line.receipt.reversal_reason=reason; line.receipt.save()
        matched=min(return_value, outstanding)
        if matched > 0:
            payable, inventory = _account('payable:suppliers'), _account('inventory:purchases')
            ensure_period_open(context.date_for(source), payable); ensure_period_open(context.date_for(source), inventory)
            batch=_batch(ret, context, 'purchase.return.liability_reversal', [(payable,'debit',matched,'عكس التزام مرتجع شراء'),(inventory,'credit',matched,'عكس مخزون مرتجع شراء')], reason=reason)
            _audit(context,'purchase_financial_return_posted',ret,{'purchase_id':source.pk,'business_date':str(ret.business_date),'return_value':str(return_value),'matched_payable_reversal':str(matched),'debit':payable.code,'credit':inventory.code,'posting_batch_id':str(batch.pk)})
        excess=return_value-matched
        if excess > 0:
            FinanceReviewItem.objects.update_or_create(issue_code='paid_purchase_return_requires_finance_resolution',record_type=ret._meta.label,record_id=str(ret.pk),defaults={'reason':'قيمة المرتجع المدفوعة تحتاج معالجة مالية يدوية؛ لم تُفترض ذمة مورد مدينة أو استرداد أو إشعار دائن.','details':{'purchase_id':source.pk,'purchase_return_id':ret.pk,'return_value':str(return_value),'matched_payable_reversal':str(matched),'unresolved_excess':str(excess),'decision_id':'D09'},'resolved_at':None})
        source.cancelled_at=timezone.now(); source.cancellation_reason=reason; source.save(update_fields=['cancelled_at','cancellation_reason','updated_at']); sync_state(source); return source
    return dispatch('purchase.cancel', purchase, context, handle)


return_purchase = cancel
reverse = cancel
