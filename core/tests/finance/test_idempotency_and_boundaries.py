from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.admin import CashMovementAdmin, ExpenseAdmin, PurchaseAdmin
from core.models import (
    CashMovement, Expense, ExpenseCategory, FinancialAccount, InventoryItem,
    PostingBatch, PostingCommand, PostingReconciliationFailure,
    Purchase, PurchaseItem, PurchasePayment, PurchaseReceipt, PurchaseReturn,
    StockMovement, Transfer,
)
from core.services.posting import expenses, purchases, transfers
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import IdempotencyConflict
from core.services.posting.reconciliation import record_unsupported_bypasses


class FinanceBoundaryTestCase(TestCase):
    def setUp(self):
        users = get_user_model()
        self.actor = users.objects.create_user(username='boundary-staff', password='x', phone='+990020', role='admin')
        self.approver = users.objects.create_superuser(username='boundary-owner', password='x', phone='+990021')
        self.day = date(2026, 8, 7)
        self.cash = FinancialAccount.objects.create(
            code='cash:boundary', name_ar='صندوق', account_type='asset', scope='cashbox', is_active=True,
            negative_balance_policy='allow',
        )

    def context(self, key, channel='service-test'):
        return PostingContext(
            actor=self.actor, approver=self.approver, business_date=self.day,
            idempotency_key=key, channel=channel, request_metadata={'request_id': key},
        )

    def purchase(self, quantity=Decimal('2'), cost=Decimal('100')):
        item = InventoryItem.objects.create(
            name_ar=f'مادة {InventoryItem.objects.count()}', unit=InventoryItem.Unit.PIECE,
            current_quantity=Decimal('0'),
        )
        purchase = Purchase.objects.create(
            business_date=self.day, supplier_name='مورد', total_syp=quantity * cost,
            subtotal_syp=quantity * cost, created_by=self.actor,
        )
        PurchaseItem.objects.create(
            purchase=purchase, inventory_item=item, quantity=quantity,
            unit=InventoryItem.Unit.PIECE, unit_cost_syp=cost,
        )
        return purchase, item

    def assert_balanced(self, batch):
        self.assertEqual(batch.entries.count(), 2)
        self.assertTrue(batch.is_balanced())
        self.assertEqual(
            sum((entry.signed_amount for entry in batch.entries.all()), Decimal('0')),
            Decimal('0'),
        )

    def test_duplicate_transfer_key_returns_original_with_complete_audit_metadata(self):
        destination = FinancialAccount.objects.create(
            code='cash:destination', name_ar='وجهة', account_type='asset', is_active=True,
            negative_balance_policy='allow',
        )
        transfer = Transfer(
            source_account=self.cash, destination_account=destination, amount=Decimal('75'),
            business_date=self.day, reason='تغذية',
        )
        first = transfers.post(transfer, self.context('transfer:retry'))
        again = transfers.post(first, self.context('transfer:retry'))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(PostingCommand.objects.filter(key='transfer:retry').count(), 1)
        self.assertEqual(PostingBatch.objects.filter(source_object_id=str(first.pk), reversal_of__isnull=True).count(), 1)
        self.assertEqual(first.movement_projections.count(), 2)
        self.assert_balanced(first.posting_batch)
        batch = first.posting_batch
        self.assertEqual((batch.actor, batch.approver, batch.channel), (self.actor, self.approver, 'service-test'))
        self.assertEqual(batch.metadata, {'request_id': 'transfer:retry'})

    def test_duplicate_purchase_payment_key_preserves_rows_and_totals(self):
        purchase, _ = self.purchase()
        first = purchases.pay(purchase, self.context('purchase-pay:retry'), 100, self.cash, 'cash')
        again = purchases.pay(purchase, self.context('purchase-pay:retry'), 100, self.cash, 'cash')
        self.assertEqual(first.pk, again.pk)
        purchase.refresh_from_db()
        self.assertEqual(PurchasePayment.objects.filter(purchase=purchase).count(), 1)
        self.assertEqual(PostingBatch.objects.filter(operation_type='purchase.payment').count(), 1)
        self.assertEqual(purchase.amount_paid_syp, Decimal('100'))
        self.assertEqual(purchase.remaining_syp, Decimal('100'))
        self.assert_balanced(first.posting_batch)
        self.assertEqual((first.actor, first.approver, first.idempotency_key), (self.actor, self.approver, 'purchase-pay:retry'))

    def test_repeated_receipt_cancellation_return_and_reversal_requests_do_not_drift(self):
        purchase, item = self.purchase()
        receipt = purchases.receive(purchase, self.context('receipt:retry'))
        receipt_again = purchases.receive(purchase, self.context('receipt:retry'))
        self.assertEqual(receipt.pk, receipt_again.pk)
        self.assertEqual(PurchaseReceipt.objects.filter(purchase=purchase).count(), 1)
        self.assertEqual(StockMovement.objects.filter(related_purchase=purchase, direction='in').count(), 1)
        item.refresh_from_db(); self.assertEqual(item.current_quantity, Decimal('2'))

        cancelled = purchases.cancel(purchase, self.context('cancel:retry'), 'إرجاع للمورد')
        cancelled_again = purchases.cancel(purchase, self.context('cancel:retry'), 'إرجاع للمورد')
        self.assertEqual(cancelled.pk, cancelled_again.pk)
        self.assertEqual(PurchaseReturn.objects.filter(purchase=purchase).count(), 1)
        self.assertEqual(StockMovement.objects.filter(related_purchase=purchase, direction='out').count(), 1)
        item.refresh_from_db(); self.assertEqual(item.current_quantity, Decimal('0'))
        self.assertEqual(PostingBatch.objects.filter(reversal_of__isnull=False).count(), 1)
        for batch in PostingBatch.objects.all():
            self.assert_balanced(batch)

    def test_one_key_cannot_be_reused_for_a_different_direct_service_command(self):
        purchase, _ = self.purchase()
        purchases.pay(purchase, self.context('shared-key'), 50, self.cash)
        expense_category = ExpenseCategory.objects.create(name_ar='تشغيل', code='boundary-ops')
        expense = Expense.objects.create(
            business_date=self.day, category=expense_category, supplier_name='مورد',
            title='مصروف', amount_syp=10,
        )
        with self.assertRaises(IdempotencyConflict):
            expenses.create_draft(expense, self.context('shared-key'))

    def test_admin_financial_models_are_read_only_and_actions_use_service_metadata(self):
        request = RequestFactory().get('/admin/core/expense/')
        request.user = self.actor
        for model_admin in (
            ExpenseAdmin(Expense, admin.site), PurchaseAdmin(Purchase, admin.site),
            CashMovementAdmin(CashMovement, admin.site),
        ):
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertEqual(
                set(model_admin.get_readonly_fields(request)),
                {field.name for field in model_admin.model._meta.fields},
            )

        category = ExpenseCategory.objects.create(name_ar='إداري', code='admin-ops')
        expense = Expense.objects.create(
            business_date=self.day, category=category, supplier_name='مورد', title='إداري',
            amount_syp=20, payment_method=Expense.PaymentMethod.CASH, financial_account=self.cash,
        )
        model_admin = ExpenseAdmin(Expense, admin.site)
        model_admin.message_user = lambda *args, **kwargs: None
        model_admin.pay_action(request, Expense.objects.filter(pk=expense.pk))
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.PAID)
        self.assertEqual(expense.payment_batch.channel, 'admin')
        self.assertEqual((expense.payment_batch.actor, expense.payment_batch.approver), (self.actor, self.actor))

    def test_repeated_staff_api_receipt_request_keeps_one_receipt_and_stock_total(self):
        purchase, item = self.purchase()
        self.client.force_login(self.actor)
        url = reverse('staff_purchase_detail', args=[purchase.pk])
        # The staff endpoint supplies a stable request id to the same service used
        # by direct integrations; replaying that API request must be harmless.
        payload = {'action': 'receive', 'idempotency_key': 'staff-receipt-retry'}
        first = self.client.post(url, payload)
        second = self.client.post(url, payload)
        self.assertEqual((first.status_code, second.status_code), (302, 302))
        self.assertEqual(PurchaseReceipt.objects.filter(purchase=purchase).count(), 1)
        self.assertEqual(StockMovement.objects.filter(related_purchase=purchase).count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.current_quantity, Decimal('2'))
        command = PostingCommand.objects.get(key='staff-receipt-retry')
        self.assertEqual((command.actor, command.channel), (self.actor, 'staff'))

    def test_unsupported_direct_mutation_is_detected_once_by_reconciliation(self):
        movement = CashMovement.objects.create(
            business_date=self.day, financial_account=self.cash,
            movement_type=CashMovement.MovementType.OTHER, direction=CashMovement.Direction.IN,
            amount_syp=Decimal('9'), title='كتابة مباشرة', created_by=self.actor,
        )
        record_unsupported_bypasses(); record_unsupported_bypasses()
        failure = PostingReconciliationFailure.objects.get(record_type='core.CashMovement', record_id=str(movement.pk))
        self.assertIn('Unsupported direct write', failure.reason)
        self.assertEqual(PostingReconciliationFailure.objects.filter(record_id=str(movement.pk)).count(), 1)
