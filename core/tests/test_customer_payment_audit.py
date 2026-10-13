import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    CashMovement, Category, FinancialAccount, HubVisit, InternetSession,
    Order, OrderItem, Payment, PostingBatch, PostingEntry, Product,
)
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect


class CustomerPaymentAuditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='cash-audit-test', password='unused', phone='+963196001',
            role='admin',
        )
        FinancialAccount.objects.create(
            code='cash:audit-customer', name_ar='صندوق تجريبي',
            account_type='asset', scope='cashbox', is_active=True,
            negative_balance_policy='allow',
        )
        FinancialAccount.objects.create(
            code='revenue:audit-customer', name_ar='إيراد تجريبي',
            account_type='revenue', scope='operating', is_active=True,
            negative_balance_policy='allow',
        )
        self.category = Category.objects.create(name_ar='أصناف الاختبار')
        self.product = Product.objects.create(
            category=self.category, name_ar='مشروب تجريبي', price_syp=100,
        )

    def order(self, amount, *, visit=None):
        order = Order.objects.create(visit=visit)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=1,
            product_name_ar_snapshot='مشروب',
            unit_price_syp_snapshot=amount,
            line_total_syp_snapshot=amount,
            selected_options_snapshot=[],
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
        return order

    def collect_cash(self, order, amount, *, key):
        return collect(
            order, PostingContext(
                actor=self.user, idempotency_key=key, channel='cashier',
            ), amount, Payment.Method.CASH,
        )

    def audit(self, **options):
        output = StringIO()
        call_command('audit_customer_payments', as_json=True, stdout=output, **options)
        return json.loads(output.getvalue())

    def test_cash_collection_matches_order_cash_projection_and_journal(self):
        order = self.order(100)
        payment = self.collect_cash(order, 100, key='customer-audit-valid')
        before = (
            Payment.objects.count(), CashMovement.objects.count(),
            PostingBatch.objects.count(), PostingEntry.objects.count(),
        )

        result = self.audit(payment_id=payment.pk)

        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['standalone_orders'][0]['remaining_syp'], 0)
        self.assertEqual(result['payments'][0]['cash_movements'], 1)
        self.assertEqual(result['payments'][0]['collection_journals'], 1)
        self.assertTrue(result['read_only'])
        self.assertEqual(before, (
            Payment.objects.count(), CashMovement.objects.count(),
            PostingBatch.objects.count(), PostingEntry.objects.count(),
        ))

    def test_partial_payment_reconciles_entire_visit_and_internet_order(self):
        visit = HubVisit.objects.create(created_by=self.user)
        first = self.order(100, visit=visit)
        second = self.order(200, visit=visit)
        paid = self.collect_cash(first, 100, key='customer-audit-partial')
        InternetSession.objects.create(
            visit=visit, linked_order=first,
            session_type=InternetSession.SessionType.INTERNET,
            status=InternetSession.Status.BILLED,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            start_time=timezone.now(),
        )

        result = self.audit(visit_code=str(visit.public_code))

        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['accounts'][0]['total_syp'], 300)
        self.assertEqual(result['accounts'][0]['paid_syp'], 100)
        self.assertEqual(result['accounts'][0]['remaining_syp'], 200)
        self.assertEqual(result['payments'][0]['id'], paid.pk)
        self.assertEqual(result['internet'][0]['session_status'], 'billed')
        self.assertEqual(result['internet'][0]['order_payment_status'], 'paid')
        self.assertEqual(second.remaining_syp, 200)

    def test_direct_cash_payment_is_flagged_without_automatic_repair(self):
        order = self.order(100)
        payment = Payment.objects.create(
            order=order, amount_syp=100, method=Payment.Method.CASH,
        )
        result = self.audit(payment_id=payment.pk)

        self.assertEqual(result['status'], 'WARN')
        self.assertEqual({x['code'] for x in result['findings']}, {
            'collection_journal_missing_or_invalid',
            'cash_movement_missing_or_mismatch',
        })
        self.assertEqual(CashMovement.objects.count(), 0)
        self.assertEqual(PostingBatch.objects.count(), 0)

    def test_reversed_payment_is_not_counted_as_active_collection(self):
        order = self.order(100)
        payment = Payment.objects.create(
            order=order, amount_syp=100, method=Payment.Method.CASH,
            is_reversed=True,
        )
        result = self.audit(payment_id=payment.pk)
        self.assertEqual(result['standalone_orders'][0]['remaining_syp'], 100)
        self.assertFalse(result['payments'][0]['counts_as_paid'])

    def test_recent_is_bounded_and_empty_visit_remains_visible(self):
        self.collect_cash(self.order(100), 100, key='customer-audit-recent-1')
        self.collect_cash(self.order(200), 200, key='customer-audit-recent-2')

        self.assertEqual(len(self.audit(recent=1)['payments']), 1)
        with self.assertRaises(CommandError):
            self.audit(recent=31)
        with self.assertRaises(CommandError):
            self.audit(payment_id=9999999)

        empty_visit = HubVisit.objects.create(created_by=self.user)
        result = self.audit(visit_code=str(empty_visit.public_code))
        self.assertEqual(result['accounts'][0]['total_syp'], 0)
        self.assertEqual(result['accounts'][0]['remaining_syp'], 0)
