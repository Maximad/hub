"""Regression tests for read-only customer payment posting reconciliation."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import (
    CashMovement, Category, FinancialAccount, Order, OrderItem, Payment,
    PostingBatch, PostingEntry, Product,
)
from core.services.finance_reconciliation import FinanceReconciler
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect


class CustomerPaymentReconciliationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='finance-payment-scan', password='unused',
            phone='+963197713', role='admin',
        )
        FinancialAccount.objects.create(
            code='cash:finance-payment-scan', name_ar='صندوق اختبار',
            account_type=FinancialAccount.AccountType.ASSET,
            scope='cashbox', is_active=True,
            negative_balance_policy=FinancialAccount.NegativeBalancePolicy.ALLOW,
        )
        FinancialAccount.objects.create(
            code='clearing:finance-payment-scan', name_ar='مقاصة اختبار',
            account_type=FinancialAccount.AccountType.CLEARING,
            scope='operating', is_active=True,
        )
        FinancialAccount.objects.create(
            code='revenue:finance-payment-scan', name_ar='إيراد اختبار',
            account_type=FinancialAccount.AccountType.REVENUE,
            scope='operating', is_active=True,
        )
        category = Category.objects.create(name_ar='اختبار المالية')
        product = Product.objects.create(
            category=category, name_ar='صنف اختبار', price_syp=500,
        )
        self.order = Order.objects.create()
        OrderItem.objects.create(
            order=self.order, product=product, quantity=1,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=500, line_total_syp_snapshot=500,
            selected_options_snapshot=[],
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )

    def collect_payment(self, method, amount=100, key='finance-scan-valid'):
        return collect(
            self.order,
            PostingContext(actor=self.user, idempotency_key=key, channel='cashier'),
            amount,
            method,
        )

    def findings(self):
        recon = FinanceReconciler()
        recon._payments()
        return recon.findings

    def test_legitimate_posted_cash_receipt_is_not_falsely_missing(self):
        payment = self.collect_payment(Payment.Method.CASH)
        before = (
            Payment.objects.count(), CashMovement.objects.count(),
            PostingBatch.objects.count(), PostingEntry.objects.count(),
        )

        self.assertEqual(self.findings(), [])
        self.assertTrue(PostingBatch.objects.filter(
            operation_type='order_payment.collect',
            source_object_id=str(payment.pk),
            status=PostingBatch.Status.POSTED,
        ).exists())
        self.assertEqual(before, (
            Payment.objects.count(), CashMovement.objects.count(),
            PostingBatch.objects.count(), PostingEntry.objects.count(),
        ))

    def test_posted_manual_transfer_is_not_misidentified_as_missing(self):
        self.collect_payment(
            Payment.Method.MANUAL_TRANSFER, key='finance-scan-transfer',
        )
        self.assertEqual(self.findings(), [])
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_legacy_cash_payment_without_journal_or_movement_is_flagged(self):
        payment = Payment.objects.create(
            order=self.order, amount_syp=100, method=Payment.Method.CASH,
        )
        codes = {row['code'] for row in self.findings()}
        self.assertEqual(codes, {
            'payment_missing_movement', 'payment_missing_posting',
        })
        self.assertEqual(
            {row['record_id'] for row in self.findings()}, {str(payment.pk)},
        )
        self.assertEqual(PostingBatch.objects.count(), 0)

    def test_legacy_manual_transfer_requires_journal_but_not_cash_movement(self):
        Payment.objects.create(
            order=self.order, amount_syp=100,
            method=Payment.Method.MANUAL_TRANSFER,
        )
        self.assertEqual(
            [row['code'] for row in self.findings()],
            ['payment_missing_posting'],
        )

    def test_unpaid_complimentary_discount_and_reversed_records_are_not_collections(self):
        for method in (
            Payment.Method.UNPAID, Payment.Method.FREE,
            Payment.Method.MEMBER_DISCOUNT,
        ):
            Payment.objects.create(
                order=self.order, amount_syp=50, method=method,
            )
        Payment.objects.create(
            order=self.order, amount_syp=50, method=Payment.Method.CASH,
            is_active=False, is_reversed=True, reversal_reason='other',
            reversed_at=timezone.now(), reversed_by=self.user,
        )
        self.assertEqual(self.findings(), [])

    def test_cancelled_original_journal_is_not_accepted_as_posted(self):
        payment = self.collect_payment(Payment.Method.CASH)
        PostingBatch.objects.filter(
            operation_type='order_payment.collect',
            source_object_id=str(payment.pk),
        ).update(status=PostingBatch.Status.CANCELLED, posted_at=None)

        self.assertEqual(
            [row['code'] for row in self.findings()],
            ['payment_missing_posting'],
        )
