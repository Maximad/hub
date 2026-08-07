from collections import Counter
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, Q, Sum

from core.models import (AuditEvent, CashMovement, DailyClose, Expense, FinanceReconciliationState,
    FinanceReviewItem, FinancialAccount, Payment, PostingBatch, PostingCommand, PostingEntry, Purchase,
    PurchasePayment, PurchaseReceipt, PurchaseReceiptLine, PurchaseReturn, StockMovement, Transfer)
from core.services.posting.closing import close_totals, snapshot_for


def _decimal(value):
    return Decimal(str(value or 0))


class FinanceReconciler:
    """Read-only integrity scan. Writes occur only in :meth:`apply_backfill`."""
    def __init__(self, start=None, end=None, account=None):
        self.start, self.end, self.account = start, end, account
        self.findings = []

    def add(self, code, obj, message, *, severity='error', review=False, **details):
        self.findings.append({'code': code, 'severity': severity, 'model': obj._meta.label,
            'record_id': str(obj.pk), 'message': message, 'review_required': review, 'details': details})

    def dated(self, qs, field='business_date'):
        if self.start: qs = qs.filter(**{field + '__gte': self.start})
        if self.end: qs = qs.filter(**{field + '__lte': self.end})
        return qs

    def run(self):
        self._expenses(); self._payments(); self._keys_and_batches(); self._orphans()
        self._purchases(); self._transfers(); self._periods(); self._audits()
        return sorted(self.findings, key=lambda x: (x['code'], x['model'], x['record_id']))

    def _expenses(self):
        for expense in self.dated(Expense.objects.select_related('financial_account', 'vendor').prefetch_related('cash_movements')):
            active = [m for m in expense.cash_movements.all() if not m.is_cancelled]
            expected = expense.affects_cashbox()
            generated = [m for m in active if m.is_generated]
            if expected and len(generated) != 1:
                self.add('cash_expense_movement_count', expense, 'Paid cash expense must have exactly one active generated movement.', active_count=len(generated))
            if not expected and active:
                self.add('stale_expense_movement', expense, 'Cancelled or non-cash expense has active cash movements.', movement_ids=[m.pk for m in active])
            for movement in generated:
                differences = {}
                expected_account = expense.financial_account_id
                expected_values = {'amount': _decimal(expense.amount_syp), 'date': expense.business_date,
                    'direction': CashMovement.Direction.OUT, 'account': expected_account, 'source': expense.pk}
                actual_values = {'amount': movement.amount_syp, 'date': movement.business_date,
                    'direction': movement.direction, 'account': movement.financial_account_id, 'source': movement.related_expense_id}
                for key in expected_values:
                    if expected_values[key] != actual_values[key]: differences[key] = {'expected': str(expected_values[key]), 'actual': str(actual_values[key])}
                if movement.movement_type != CashMovement.MovementType.CASH_EXPENSE: differences['movement_type'] = {'expected': 'cash_expense', 'actual': movement.movement_type}
                if differences: self.add('expense_movement_mismatch', movement, 'Cash movement projection differs from its expense.', differences=differences)
            # Identity collisions require people, never a fuzzy match.
            if expense.vendor_id and expense.supplier_name.strip() and expense.supplier_name.strip() not in {expense.vendor.name_ar, expense.vendor.name_en}:
                self.add('ambiguous_supplier_identity', expense, 'Registered vendor and free-text supplier disagree.', review=True,
                         vendor_id=expense.vendor_id, supplier_name=expense.supplier_name)

    def _payments(self):
        for payment in Payment.objects.select_related('order').prefetch_related('cash_movements'):
            movements = [m for m in payment.cash_movements.all() if not m.is_cancelled]
            if payment.is_active and not payment.is_reversed and payment.method == Payment.Method.CASH and len(movements) != 1:
                self.add('payment_missing_movement', payment, 'Active cash payment must have one cash movement.', count=len(movements))
            # The legacy order-payment workflow has no ledger batch: report it explicitly.
            if payment.is_active and not payment.is_reversed:
                self.add('payment_missing_posting', payment, 'Active order payment has no ledger posting batch.')

    def _keys_and_batches(self):
        key_rows = []
        for obj in PostingCommand.objects.all(): key_rows.append((obj.key, obj))
        for obj in self.dated(PostingBatch.objects.all()): key_rows.append((obj.idempotency_key, obj))
        for model in (PurchaseReceipt, PurchasePayment, PurchaseReturn):
            for obj in self.dated(model.objects.all()): key_rows.append((obj.idempotency_key, obj))
        counts = Counter(key for key, _ in key_rows if key)
        for key, obj in key_rows:
            if key and counts[key] > 1: self.add('duplicate_source_key', obj, 'Idempotency/source key is reused.', key=key, occurrences=counts[key])
        batches = self.dated(PostingBatch.objects.annotate(debits=Sum('entries__debit'), credits=Sum('entries__credit')))
        if self.account: batches = batches.filter(entries__account=self.account).distinct()
        for batch in batches:
            if _decimal(batch.debits) != _decimal(batch.credits):
                self.add('unbalanced_posting_batch', batch, 'Posting batch debits and credits do not balance.', debits=str(batch.debits or 0), credits=str(batch.credits or 0))

    def _orphans(self):
        for batch in self.dated(PostingBatch.objects.select_related('source_content_type', 'reversal_of')):
            if batch.source_content_type_id and batch.source is None: self.add('orphaned_posting', batch, 'Posting source no longer exists.')
            if batch.reversal_of_id and batch.reversal_of is None: self.add('orphaned_reversal', batch, 'Reversal has no original posting.')
        for movement in self.dated(CashMovement.objects.filter(is_generated=True)):
            if not any((movement.related_expense_id, movement.related_order_id, movement.related_payment_id, movement.transfer_id)):
                self.add('orphaned_cash_movement', movement, 'Generated cash movement has no source.')
        for movement in self.dated(StockMovement.objects.filter(movement_type__in=['purchase_received', 'return_to_vendor'])):
            if movement.movement_type == 'purchase_received' and not movement.purchase_receipt_line_id: self.add('orphaned_stock_movement', movement, 'Purchase receipt stock movement has no receipt line.')
            if movement.movement_type == 'return_to_vendor' and not movement.purchase_return_line_id: self.add('orphaned_stock_movement', movement, 'Return stock movement has no return line.')
        for line in PurchaseReceiptLine.objects.select_related('receipt'):
            if not hasattr(line, 'stock_movement'): self.add('orphaned_receipt_row', line, 'Receipt line has no stock movement.')

    def _purchases(self):
        purchases = self.dated(Purchase.objects.prefetch_related('items', 'payments', 'receipts__lines', 'returns__lines').select_related('vendor'))
        payable = FinancialAccount.objects.filter(code='payable:suppliers').first()
        for purchase in purchases:
            item_sum = sum((_decimal(i.line_total_syp) for i in purchase.items.all()), Decimal('0'))
            expected_total = max(item_sum - _decimal(purchase.discount_syp), Decimal('0'))
            if _decimal(purchase.subtotal_syp) != item_sum or _decimal(purchase.total_syp) != expected_total:
                self.add('purchase_total_mismatch', purchase, 'Purchase totals differ from item sums.', item_sum=str(item_sum), expected_total=str(expected_total))
            history = sum((_decimal(p.amount_syp) for p in purchase.payments.all() if not p.reversed_at), Decimal('0'))
            status_paid = _decimal(purchase.total_syp) if purchase.status == Purchase.Status.PAID else history
            if status_paid != history: self.add('purchase_paid_total_mismatch', purchase, 'Purchase paid status/total differs from payment history.', expected=str(status_paid), history=str(history))
            for receipt in purchase.receipts.all():
                if receipt.reversed_at: continue
                for line in receipt.lines.all():
                    movement = getattr(line, 'stock_movement', None)
                    if not movement or movement.is_cancelled or movement.quantity != line.received_quantity:
                        self.add('received_quantity_mismatch', line, 'Received quantity is inconsistent with stock movement.', received=str(line.received_quantity), moved=str(getattr(movement, 'quantity', 0)))
            if purchase.vendor_id and purchase.supplier_name.strip() and purchase.supplier_name.strip() not in {purchase.vendor.name_ar, purchase.vendor.name_en}:
                self.add('ambiguous_supplier_identity', purchase, 'Registered vendor and free-text supplier disagree.', review=True, vendor_id=purchase.vendor_id, supplier_name=purchase.supplier_name)
            # Liability entries should equal receipts less returns and supplier payments.
            if payable and purchase.vendor_id:
                source_ids = [str(purchase.pk)]
                ledger = PostingEntry.objects.filter(account=payable, batch__source_object_id__in=source_ids, batch__status__in=['posted', 'reversed']).aggregate(d=Sum('debit'), c=Sum('credit'))
                expected = _decimal(purchase.total_syp) - history - sum((_decimal(l.returned_quantity * l.purchase_item.unit_cost_syp) for r in purchase.returns.all() if not r.reversed_at for l in r.lines.all()), Decimal('0'))
                actual = _decimal(ledger['c']) - _decimal(ledger['d'])
                if actual != expected: self.add('supplier_balance_mismatch', purchase, 'Supplier liability differs from receipts, payments, and returns.', expected=str(expected), ledger=str(actual))

    def _transfers(self):
        qs = self.dated(Transfer.objects.prefetch_related('movement_projections'))
        if self.account: qs = qs.filter(Q(source_account=self.account) | Q(destination_account=self.account))
        for transfer in qs.filter(state__in=['posted', 'reversed']):
            legs = [m for m in transfer.movement_projections.all() if not m.is_cancelled and m.transfer_leg in ('outgoing', 'incoming')]
            incoming = sum((_decimal(m.amount_syp) for m in legs if m.direction == 'in'), Decimal('0'))
            outgoing = sum((_decimal(m.amount_syp) for m in legs if m.direction == 'out'), Decimal('0'))
            if len(legs) != 2 or incoming != outgoing or incoming != transfer.amount:
                self.add('transfer_legs_mismatch', transfer, 'Transfer is one-sided or its legs are unequal.', incoming=str(incoming), outgoing=str(outgoing), leg_count=len(legs))

    def _periods(self):
        for batch in self.dated(PostingBatch.objects.filter(status__in=['posted', 'reversed'])):
            accounts = list(batch.entries.values_list('account_id', flat=True).distinct())
            closed = DailyClose.objects.filter(account_id__in=accounts, business_date=batch.business_date, is_finalized=True, closed_at__lt=batch.created_at)
            if closed.exists(): self.add('posting_in_closed_period', batch, 'Posting was created after its account period was closed.', close_ids=list(closed.values_list('pk', flat=True)))
        closes = self.dated(DailyClose.objects.filter(is_finalized=True).select_related('account'))
        if self.account: closes = closes.filter(account=self.account)
        for close in closes:
            expected = snapshot_for(close, close_totals(close.account, close.business_date))
            comparable = {k: close.close_snapshot.get(k) for k in expected}
            if comparable != expected: self.add('close_snapshot_mismatch', close, 'Close snapshot is inconsistent with current postings.', expected=expected, actual=comparable)

    def _audits(self):
        for event in AuditEvent.objects.select_related('source_content_type'):
            if not event.actor_id: self.add('audit_missing_actor', event, 'Audit event has no actor.')
            if not event.request_key or not event.source_content_type_id or not event.source_object_id or event.source is None:
                self.add('audit_missing_operation', event, 'Audit event is not linked to a durable request and source operation.')

    @transaction.atomic
    def apply_backfill(self):
        """Apply only deterministic expense projections; persist ambiguity for review."""
        applied = 0
        for finding in self.run():
            if finding['review_required']:
                FinanceReviewItem.objects.update_or_create(issue_code=finding['code'], record_type=finding['model'], record_id=finding['record_id'],
                    defaults={'reason': finding['message'], 'details': finding['details']})
        for expense in self.dated(Expense.objects.select_related('financial_account', 'vendor')):
            state, created = FinanceReconciliationState.objects.get_or_create(operation='expense_cash_projection_v1', record_type=expense._meta.label, record_id=str(expense.pk))
            if not created and state.status == 'completed': continue
            active = expense.cash_movements.filter(is_generated=True, is_cancelled=False)
            if expense.affects_cashbox() and active.count() <= 1:
                CashMovement.objects.update_or_create(related_expense=expense, is_generated=True, is_cancelled=False,
                    defaults={'business_date': expense.business_date, 'financial_account': expense.financial_account,
                    'movement_type': 'cash_expense', 'direction': 'out', 'amount_syp': expense.amount_syp,
                    'vendor': expense.vendor, 'title': expense.title, 'notes': expense.description,
                    'created_by': expense.paid_by, 'approved_by': expense.approved_by})
                applied += 1
            elif not expense.affects_cashbox():
                applied += active.update(is_cancelled=True, cancellation_reason='Finance reconciliation backfill: source is no longer an active cash expense.')
            else:
                FinanceReviewItem.objects.update_or_create(issue_code='cash_expense_movement_count', record_type=expense._meta.label, record_id=str(expense.pk),
                    defaults={'reason': 'Multiple projections require manual selection.', 'details': {'movement_ids': list(active.values_list('pk', flat=True))}})
            state.status = 'completed'; state.details = {'applied': applied}; state.save()
        return applied
