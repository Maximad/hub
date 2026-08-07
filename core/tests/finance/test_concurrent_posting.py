from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest import SkipTest

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from core.models import (
    CashMovement, Category, DailyClose, Expense, ExpenseCategory,
    FinancialAccount, Order, OrderItem, Payment, PostingBatch, PostingCommand,
    Product,
)
from core.services.posting import closing, expenses, order_payments
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import ClosedPeriodError, InvalidTransition


class PostgreSQLConcurrentPostingTests(TransactionTestCase):
    """Real row-lock tests; SQLite cannot provide the required worker semantics."""

    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != 'postgresql':
            raise SkipTest('requires PostgreSQL SELECT FOR UPDATE semantics')

    def setUp(self):
        user = get_user_model()
        self.actor = user.objects.create_user(username='posting-worker', password='x', phone='+990010')
        self.approver = user.objects.create_user(username='posting-approver', password='x', phone='+990011', role='admin')
        self.cash = FinancialAccount.objects.create(
            code='cash:concurrency', name_ar='صندوق الاختبار', account_type='asset', scope='cashbox',
            is_active=True, negative_balance_policy='allow',
        )
        self.day = date(2026, 8, 7)

    def context(self, key):
        return PostingContext(
            actor=self.actor, approver=self.approver, business_date=self.day,
            idempotency_key=key, channel='concurrent-test', request_metadata={'worker': key},
        )

    def concurrently(self, *calls):
        def invoke(call):
            close_old_connections()
            try:
                return ('ok', call())
            except Exception as error:  # assertions below deliberately inspect loser type
                return ('error', error)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(invoke, calls))

    def expense(self):
        category = ExpenseCategory.objects.create(name_ar='تشغيل', code=f'ops-{Expense.objects.count()}')
        return Expense.objects.create(
            business_date=self.day, category=category, payee_type=Expense.PayeeType.MANUAL,
            supplier_name='مورد', title='مصروف نقدي', amount_syp=300,
        )

    def order(self):
        category = Category.objects.create(name_ar='مبيعات')
        product = Product.objects.create(category=category, name_ar='صنف', price_syp=600)
        order = Order.objects.create()
        OrderItem.objects.create(
            order=order, product=product, quantity=1, product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=600, line_total_syp_snapshot=600,
        )
        return order

    def test_two_simultaneous_cash_expense_postings_create_one_active_projection(self):
        expense = self.expense()
        results = self.concurrently(
            lambda: expenses.pay_immediately(expense, self.context('expense:a'), self.cash, Expense.PaymentMethod.CASH),
            lambda: expenses.pay_immediately(expense, self.context('expense:b'), self.cash, Expense.PaymentMethod.CASH),
        )
        self.assertEqual([state for state, _ in results].count('ok'), 1)
        self.assertIsInstance(next(value for state, value in results if state == 'error'), InvalidTransition)
        expense.refresh_from_db()
        batches = PostingBatch.objects.filter(source_object_id=str(expense.pk), status=PostingBatch.Status.POSTED)
        self.assertEqual(batches.count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_expense=expense, is_generated=True, is_cancelled=False).count(), 1)
        self.assertEqual(expense.amount_syp, 300)
        batch = batches.get()
        self.assertEqual((batch.actor_id, batch.approver_id, batch.channel), (self.actor.pk, self.approver.pk, 'concurrent-test'))
        self.assertEqual(batch.metadata, {'worker': batch.idempotency_key.removesuffix(':batch')})

    def test_two_simultaneous_order_payments_cannot_both_spend_remaining_balance(self):
        order = self.order()
        results = self.concurrently(
            lambda: order_payments.collect(order, self.context('order:a'), 600, Payment.Method.CASH),
            lambda: order_payments.collect(order, self.context('order:b'), 600, Payment.Method.CASH),
        )
        self.assertEqual([state for state, _ in results].count('ok'), 1)
        self.assertIsInstance(next(value for state, value in results if state == 'error'), InvalidTransition)
        self.assertEqual(Payment.objects.filter(order=order, is_active=True).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=order, is_cancelled=False).count(), 1)
        self.assertEqual(sum(Payment.objects.filter(order=order).values_list('amount_syp', flat=True)), 600)

    def test_close_racing_with_expense_serializes_to_one_consistent_outcome(self):
        expense = self.expense()
        daily_close = DailyClose.objects.create(business_date=self.day, account=self.cash)
        results = self.concurrently(
            lambda: closing.close(daily_close, self.context('close:a'), 0),
            lambda: expenses.pay_immediately(expense, self.context('expense:close-race'), self.cash, Expense.PaymentMethod.CASH),
        )
        self.assertEqual([state for state, _ in results].count('ok'), 1)
        loser = next(value for state, value in results if state == 'error')
        self.assertTrue(isinstance(loser, (ClosedPeriodError, InvalidTransition)))
        daily_close.refresh_from_db(); expense.refresh_from_db()
        if daily_close.status == DailyClose.Status.CLOSED:
            self.assertEqual(expense.status, Expense.Status.DRAFT)
            self.assertEqual(daily_close.expected_cash_syp, 0)
        else:
            self.assertEqual(expense.status, Expense.Status.PAID)

    def test_same_idempotency_key_in_two_workers_returns_one_result(self):
        order = self.order()
        results = self.concurrently(
            lambda: order_payments.collect(order, self.context('order:duplicate'), 600, Payment.Method.CASH),
            lambda: order_payments.collect(order, self.context('order:duplicate'), 600, Payment.Method.CASH),
        )
        self.assertEqual([state for state, _ in results], ['ok', 'ok'])
        self.assertEqual(results[0][1].pk, results[1][1].pk)
        self.assertEqual(PostingCommand.objects.filter(key='order:duplicate').count(), 1)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=order).count(), 1)
