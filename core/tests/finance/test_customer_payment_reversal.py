"""D03 customer payment reversal: verified original, inverse journal and refund UI."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog, CancellationReason, CashMovement, Category, DailyClose,
    FinancialAccount, HubVisit, Order, OrderItem, Payment, PostingBatch,
    PostingCommand, Product,
)
from core.services.posting import order_payments
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import ClosedPeriodError, InvalidTransition


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class CustomerPaymentReversalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='customer-refund-admin', phone='984001',
            password='test', role='admin',
        )
        self.cashier = User.objects.create_user(
            username='customer-refund-cashier', phone='984002',
            password='test', role='cashier',
        )
        self.day = date(2026, 10, 12)
        self.cash = FinancialAccount.objects.create(
            code='cash:refund-test', name_ar='صندوق', account_type='asset',
            scope='cashbox', is_active=True, negative_balance_policy='allow',
        )
        self.clearing = FinancialAccount.objects.create(
            code='clearing:refund-test', name_ar='مقاصة',
            account_type=FinancialAccount.AccountType.CLEARING,
            is_active=True, negative_balance_policy='allow',
        )
        self.revenue = FinancialAccount.objects.create(
            code='revenue:refund-test', name_ar='إيراد',
            account_type=FinancialAccount.AccountType.REVENUE, is_active=True,
            negative_balance_policy='allow',
        )
        category = Category.objects.create(name_ar='تصحيحات')
        product = Product.objects.create(category=category, name_ar='شاي', price_syp=500)
        self.visit = HubVisit.objects.create()
        self.order = Order.objects.create(visit=self.visit)
        OrderItem.objects.create(
            order=self.order, product=product, quantity=1,
            product_name_ar_snapshot='شاي',
            unit_price_syp_snapshot=500, line_total_syp_snapshot=500,
        )

    def context(self, key, *, actor=None, day=None):
        return PostingContext(
            actor=actor or self.admin,
            approver=actor or self.admin,
            business_date=day or self.day,
            idempotency_key=key,
            channel='finance-test',
            request_metadata={'case': key, 'refund_completed': True, 'refund_reference': f'refund-evidence-{key}'},
        )

    def collected(self, method=Payment.Method.CASH):
        return order_payments.collect(
            self.order, self.context('collect:' + method),
            500, method,
        )

    def original(self, payment):
        return PostingBatch.objects.get(
            source_object_id=str(payment.pk),
            operation_type='order_payment.collect',
        )

    def test_cash_reversal_links_balanced_opposite_journal_and_refund_cash(self):
        payment = self.collected()
        original = self.original(payment)
        original_entries = list(original.entries.order_by('pk').values(
            'account_id', 'debit', 'credit',
        ))
        reversed_payment = order_payments.reverse(
            payment, self.context('refund:cash'),
            'إرجاع مبلغ قبض بالخطأ',
            reason_code=CancellationReason.STAFF_MISTAKE,
        )
        reversed_payment.refresh_from_db()
        self.assertTrue(reversed_payment.is_reversed)
        self.assertFalse(reversed_payment.is_active)
        self.assertEqual(reversed_payment.reversal_reason, CancellationReason.STAFF_MISTAKE)
        self.assertEqual(reversed_payment.reversed_by, self.admin)

        batch = PostingBatch.objects.get(operation_type='order_payment.reverse')
        self.assertEqual(batch.status, PostingBatch.Status.POSTED)
        self.assertEqual(batch.reversal_of_id, original.pk)
        self.assertEqual(batch.actor_id, self.admin.pk)
        self.assertEqual(batch.approver_id, self.admin.pk)
        self.assertEqual(batch.reason, 'إرجاع مبلغ قبض بالخطأ')
        self.assertEqual(batch.business_date, self.day)
        self.assertTrue(batch.is_balanced())
        entries = list(batch.entries.order_by('pk').values(
            'account_id', 'debit', 'credit',
        ))
        self.assertEqual(len(entries), 2)
        for before, after in zip(original_entries, entries):
            self.assertEqual(after['account_id'], before['account_id'])
            self.assertEqual(after['debit'], before['credit'])
            self.assertEqual(after['credit'], before['debit'])
        original.refresh_from_db()
        self.assertEqual(original.status, PostingBatch.Status.POSTED)
        self.assertEqual(original_entries, list(original.entries.order_by('pk').values(
            'account_id','debit','credit',
        )))
        movements = list(CashMovement.objects.filter(related_payment=payment).order_by('pk'))
        self.assertEqual(len(movements), 2)
        self.assertEqual(
            {(m.direction, m.amount_syp) for m in movements},
            {('in', Decimal('500')), ('out', Decimal('500'))},
        )
        self.assertTrue(ActivityLog.objects.filter(
            action='payment_reversed', details__payment_id=payment.pk,
        ).exists())
        self.assertEqual(self.order.paid_syp, 0)

    def test_non_cash_reversal_has_inverse_journal_without_cash_projection(self):
        payment = self.collected(Payment.Method.MANUAL_TRANSFER)
        reversed_payment = order_payments.reverse(
            payment, self.context('refund:transfer'), 'تحويل معاد للزبون',
        )
        self.assertTrue(reversed_payment.is_reversed)
        self.assertEqual(
            CashMovement.objects.filter(related_payment=payment).count(), 0,
        )
        batch = PostingBatch.objects.get(operation_type='order_payment.reverse')
        self.assertTrue(batch.is_balanced())
        self.assertEqual(batch.entries.get(account=self.clearing).credit, Decimal('500'))

    def test_duplicate_key_is_exactly_once_and_new_key_is_rejected(self):
        payment = self.collected()
        first = order_payments.reverse(payment, self.context('refund:same'), 'تصحيح التحصيل')
        second = order_payments.reverse(payment, self.context('refund:same'), 'تصحيح التحصيل')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.reverse').count(), 1)
        self.assertEqual(CashMovement.objects.filter(
            related_payment=payment, direction='out',
        ).count(), 1)
        with self.assertRaises(InvalidTransition):
            order_payments.reverse(payment, self.context('refund:new'), 'تصحيح التحصيل')

    def test_cashier_is_not_authorized_even_with_a_finance_approver(self):
        payment = self.collected()
        context = PostingContext(
            actor=self.cashier, approver=self.admin, business_date=self.day,
            idempotency_key='refund:unauthorized',
        )
        with self.assertRaises(InvalidTransition):
            order_payments.reverse(payment, context, 'ممنوع')
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        self.assertFalse(PostingBatch.objects.filter(operation_type='order_payment.reverse').exists())

    def test_closed_date_fails_without_any_financial_mutation(self):
        payment = self.collected()
        DailyClose.objects.create(
            business_date=self.day, account=self.cash,
            status=DailyClose.Status.CLOSED, is_finalized=True,
        )
        with self.assertRaises(ClosedPeriodError):
            order_payments.reverse(payment, self.context('refund:closed'), 'تصحيح بعد الإغلاق')
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.reverse').count(), 0)
        self.assertEqual(CashMovement.objects.filter(
            related_payment=payment, direction='out',
        ).count(), 0)

    def test_unposted_legacy_payment_fails_closed(self):
        payment = Payment.objects.create(
            order=self.order, amount_syp=100, method=Payment.Method.CASH,
            created_by=self.admin,
        )
        with self.assertRaises(InvalidTransition):
            order_payments.reverse(payment, self.context('refund:legacy'), 'عكس دفعة قديمة')
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        self.assertFalse(PostingBatch.objects.filter(operation_type='order_payment.reverse').exists())

    def test_missing_original_cash_movement_fails_closed(self):
        payment = self.collected()
        CashMovement.objects.filter(related_payment=payment).delete()
        with self.assertRaises(InvalidTransition):
            order_payments.reverse(payment, self.context('refund:no-cash'), 'حركة نقد مفقودة')
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)

    def test_invalid_reason_is_rejected(self):
        payment = self.collected()
        for reason, code in [('', CancellationReason.OTHER), ('x', CancellationReason.OTHER), ('تفصيل مقبول', 'invalid')]:
            with self.subTest(reason=reason, code=code):
                with self.assertRaises(InvalidTransition):
                    order_payments.reverse(payment, self.context('refund:bad'), reason, reason_code=code)
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)

    def test_ui_requires_finance_permission_and_customer_refund_confirmation(self):
        payment = self.collected()
        url = reverse('staff_customer_payment_reverse', kwargs={'payment_id': payment.pk})
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.client.force_login(self.admin)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'إرجاع المبلغ')
        self.assertContains(page, 'name="idempotency_key"')
        token = str(uuid4())
        not_confirmed = self.client.post(url, {
            'idempotency_key': token, 'reason': 'تصحيح تحصيل الزبون',
            'reason_code': CancellationReason.STAFF_MISTAKE,
        })
        self.assertEqual(not_confirmed.status_code, 200)
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        completed = self.client.post(url, {
            'idempotency_key': token,
            'reason': 'تصحيح تحصيل الزبون',
            'reason_code': CancellationReason.STAFF_MISTAKE,
            'refund_completed': '1',
            'refund_reference': 'نقداً بموجب إيصال الاسترجاع 42',
        })
        self.assertEqual(completed.status_code, 302)
        payment.refresh_from_db()
        self.assertTrue(payment.is_reversed)

    def test_cashier_drawer_shows_finance_links_only_to_finance_users(self):
        self.collected()
        url = reverse('staff_cashier_order', kwargs={'public_code': self.visit.public_code})
        self.client.force_login(self.admin)
        page = self.client.get(url, {'panel': 'payment'})
        self.assertContains(page, 'مراجعة / عكس الدفعة')
        self.client.force_login(self.cashier)
        page = self.client.get(url, {'panel': 'payment'})
        self.assertNotContains(page, 'مراجعة / عكس الدفعة')

    def test_reversing_one_partial_collection_preserves_other_payment(self):
        first = order_payments.collect(
            self.order, self.context('collect:partial-one'),
            200, Payment.Method.CASH,
        )
        second = order_payments.collect(
            self.order, self.context('collect:partial-two'),
            300, Payment.Method.CASH,
        )
        self.assertEqual(self.order.paid_syp, Decimal('500'))
        order_payments.reverse(
            first, self.context('refund:partial-one'), 'تصحيح الدفعة الجزئية',
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_reversed)
        self.assertTrue(second.is_active)
        self.assertFalse(second.is_reversed)
        self.assertEqual(self.order.paid_syp, Decimal('300'))
        self.assertEqual(
            CashMovement.objects.get(
                related_payment=first, direction=CashMovement.Direction.OUT,
            ).amount_syp, Decimal('200'),
        )
        self.assertEqual(
            PostingBatch.objects.filter(operation_type='order_payment.reverse').count(), 1,
        )

    def test_downstream_lifecycle_exception_rolls_back_entire_reversal(self):
        from unittest.mock import patch

        payment = self.collected()
        original = self.original(payment)
        original_entries = list(original.entries.order_by('pk').values(
            'account_id', 'debit', 'credit',
        ))
        with patch(
            'core.services.internet_lifecycle.apply_payment_reversal',
            side_effect=RuntimeError('test lifecycle failure'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'test lifecycle failure'):
                order_payments.reverse(
                    payment, self.context('refund:failed-lifecycle'),
                    'اختبار التراجع عند فشل التسويات',
                )
        payment.refresh_from_db()
        original.refresh_from_db()
        self.assertTrue(payment.is_active)
        self.assertFalse(payment.is_reversed)
        self.assertEqual(original.status, PostingBatch.Status.POSTED)
        self.assertEqual(
            list(original.entries.order_by('pk').values('account_id', 'debit', 'credit')),
            original_entries,
        )
        self.assertFalse(
            PostingBatch.objects.filter(operation_type='order_payment.reverse').exists(),
        )
        self.assertFalse(
            PostingCommand.objects.filter(key='refund:failed-lifecycle').exists(),
        )
        self.assertFalse(CashMovement.objects.filter(
            related_payment=payment, direction=CashMovement.Direction.OUT,
        ).exists())
        self.assertFalse(ActivityLog.objects.filter(
            action='payment_reversed', details__payment_id=payment.pk,
        ).exists())

    def test_inactive_financial_account_blocks_reversal_without_side_effects(self):
        payment = self.collected()
        FinancialAccount.objects.filter(pk=self.revenue.pk).update(is_active=False)
        with self.assertRaises(InvalidTransition):
            order_payments.reverse(
                payment, self.context('refund:inactive-account'), 'حساب مالي غير فعّال',
            )
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        self.assertFalse(PostingBatch.objects.filter(
            operation_type='order_payment.reverse',
        ).exists())
        self.assertFalse(PostingCommand.objects.filter(
            key='refund:inactive-account',
        ).exists())

    def test_closed_original_day_can_be_reversed_on_new_open_day(self):
        from datetime import timedelta

        payment = self.collected()
        DailyClose.objects.create(
            business_date=self.day, account=self.cash,
            status=DailyClose.Status.CLOSED, is_finalized=True,
        )
        new_day = self.day + timedelta(days=1)
        order_payments.reverse(
            payment, self.context('refund:next-open-day', day=new_day),
            'إعادة المبلغ بتاريخ عمل مفتوح',
        )
        batch = PostingBatch.objects.get(operation_type='order_payment.reverse')
        self.assertEqual(batch.business_date, new_day)
        self.assertEqual(self.original(payment).business_date, self.day)
        self.assertEqual(
            CashMovement.objects.get(
                related_payment=payment, direction=CashMovement.Direction.OUT,
            ).business_date, new_day,
        )

    def test_malformed_original_posted_journal_fails_closed(self):
        payment = self.collected()
        original = self.original(payment)
        # Simulate historical data corruption via a fixture-only direct update.
        original.entries.filter(credit__isnull=False).update(credit=Decimal('499'))
        with self.assertRaises(InvalidTransition):
            order_payments.reverse(
                payment, self.context('refund:malformed-journal'),
                'قيد أصلي غير متوازن',
            )
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        self.assertFalse(
            PostingBatch.objects.filter(operation_type='order_payment.reverse').exists(),
        )

    def test_csrf_protected_finance_confirmation_rejects_untokened_post(self):
        from django.test import Client

        payment = self.collected()
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.admin)
        url = reverse('staff_customer_payment_reverse', kwargs={'payment_id': payment.pk})
        response = strict_client.post(url, {
            'idempotency_key': str(uuid4()),
            'reason': 'تصحيح دفعة مسجلة',
            'reason_code': CancellationReason.OTHER,
            'refund_completed': '1',
            'refund_reference': 'إيصال معتمد 42',
        })
        self.assertEqual(response.status_code, 403)
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)
        self.assertFalse(
            PostingBatch.objects.filter(operation_type='order_payment.reverse').exists(),
        )

