"""Regression tests for exactly-once standalone cashier collections."""
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import (ActivityLog, CashMovement, CurrencyEntrySnapshot,
                         HubVisit, Order, Payment, PostingBatch, PostingCommand, PostingEntry)
from core.tests import test_visit_cashier_settlement as fixtures

WEB = dict(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
           STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
MONEY = (Payment, CashMovement, CurrencyEntrySnapshot, PostingBatch, PostingEntry, PostingCommand)


@override_settings(**WEB)
class StandaloneCashierRecoveryTests(TestCase):
    setUp = fixtures.VisitCashierSettlementTests.setUp
    tearDown = fixtures.VisitCashierSettlementTests.tearDown
    order = fixtures.VisitCashierSettlementTests.order

    def urls(self, order):
        return (reverse('staff_cashier_order', args=[order.public_code]),
                reverse('staff_cashier_pay', args=[order.public_code]))

    def payload(self, key, amount='100', **extra):
        return {
            'idempotency_key': key, 'currency_amount': amount,
            'currency_currency': 'SYP_NEW', 'method': 'cash',
            'notes': 'ملاحظات محفوظة', **extra,
        }

    def counts(self):
        return tuple(model.objects.count() for model in MONEY)

    def test_full_and_drawer_forms_use_distinct_attempt_keys(self):
        order = self.order(300)
        page, _pay = self.urls(order)
        full = self.client.get(page)
        drawer = self.client.get(page + '?panel=payment')
        self.assertEqual(full.status_code, 200)
        self.assertEqual(drawer.status_code, 200)
        self.assertNotEqual(full.context['collection_request_key'],
                            drawer.context['collection_request_key'])
        self.assertContains(full, 'name="currency_amount"')
        self.assertContains(full, 'value="300"')
        self.assertContains(drawer, 'value="300"')

    def test_late_snapshot_failure_rolls_every_money_record_back_then_retries(self):
        order = self.order(100)
        page, pay = self.urls(order)
        initial = self.counts()
        payload = self.payload('standalone:late-failure')
        with patch('core.views.staff_standalone_payments.CurrencyEntryFormService.snapshot',
                   side_effect=RuntimeError('late database failure')):
            failed = self.client.post(pay, payload)
        self.assertEqual(failed.status_code, 200)
        self.assertTemplateUsed(failed, 'staff/cashier_order.html')
        self.assertEqual(self.counts(), initial)
        self.assertEqual(failed.context['collection_request_key'], payload['idempotency_key'])
        self.assertContains(failed, 'name="idempotency_key" value="standalone:late-failure"')
        self.assertContains(failed, 'value="100"')
        self.assertContains(failed, 'ملاحظات محفوظة')
        self.assertNotContains(failed, 'late database failure')

        succeeded = self.client.post(pay, payload)
        self.assertEqual(succeeded.status_code, 302)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=order).count(), 1)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.collect').count(), 1)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), initial[2] + 1)
        self.assertEqual(order.remaining_syp, 0)

        # The settled balance must not make the original successful POST fail.
        replay = self.client.post(pay, payload)
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), initial[2] + 1)

    def test_failed_drawer_preserves_retry_key_currency_notes_and_no_manager_password(self):
        order = self.order(250)
        _page, pay = self.urls(order)
        self.client.force_login(self.cashier)
        original = self.payload('standalone:drawer-partial', amount='100',
                                manager_username='wrong', manager_password='wrongpass')
        failed = self.client.post(pay + '?panel=payment', original)
        self.assertEqual(failed.status_code, 200)
        self.assertTemplateUsed(failed, 'staff/_payment_panel.html')
        self.assertEqual(failed.context['collection_request_key'], original['idempotency_key'])
        self.assertContains(failed, 'name="idempotency_key" value="standalone:drawer-partial"')
        self.assertContains(failed, 'value="100"')
        self.assertContains(failed, 'ملاحظات محفوظة')
        self.assertContains(failed, 'الدفع الجزئي يحتاج موافقة')
        self.assertNotContains(failed, 'wrongpass')
        self.assertFalse(Payment.objects.filter(order=order).exists())

        corrected = {**original, 'manager_username': self.admin.username,
                     'manager_password': 'adminpass'}
        response = self.client.post(pay + '?panel=payment', corrected)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.context['collection_request_key'], original['idempotency_key'])
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='partial_payment_approved').count(), 1)

    def test_approval_audit_failure_is_atomic_and_recoverable(self):
        order = self.order(200)
        _page, pay = self.urls(order)
        payload = self.payload('standalone:approval-audit', amount='100')
        before = self.counts()
        with patch('core.views.staff_standalone_payments.ActivityLog.objects.create',
                   side_effect=RuntimeError('late audit failure')):
            failed = self.client.post(pay, payload)
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(self.counts(), before)
        self.assertEqual(failed.context['collection_request_key'], payload['idempotency_key'])
        ok = self.client.post(pay, payload)
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_changed_intent_and_different_cashier_cannot_reuse_key(self):
        order = self.order(300)
        _page, pay = self.urls(order)
        self.assertEqual(self.client.post(pay, self.payload('standalone:intent')).status_code, 302)
        for changed in (self.payload('standalone:intent', amount='101'),
                        self.payload('standalone:intent', method='manual_transfer'),
                        self.payload('standalone:intent', notes='ملاحظة جديدة')):
            denied = self.client.post(pay, changed)
            self.assertEqual(denied.status_code, 200)
            self.assertContains(denied, 'تغيرت تفاصيل الدفع')
        self.client.force_login(self.cashier)
        denied = self.client.post(pay, self.payload('standalone:intent'))
        self.assertEqual(denied.status_code, 200)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_foreign_order_and_wrong_command_key_are_rejected(self):
        one, two = self.order(200), self.order(200)
        _page, first_pay = self.urls(one)
        _page, second_pay = self.urls(two)
        original = self.payload('standalone:shared-key')
        self.assertEqual(self.client.post(first_pay, original).status_code, 302)
        rejected = self.client.post(second_pay, original)
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(Payment.objects.filter(order=one).count(), 1)
        self.assertFalse(Payment.objects.filter(order=two).exists())

    def test_distinct_tokens_allow_real_identical_partial_collections(self):
        order = self.order(300)
        page, pay = self.urls(order)
        first_key = self.client.get(page).context['collection_request_key']
        self.client.post(pay, self.payload(first_key))
        next_key = self.client.get(page).context['collection_request_key']
        self.assertNotEqual(first_key, next_key)
        self.client.post(pay, self.payload(next_key))
        self.assertEqual(order.remaining_syp, 100)
        self.assertEqual(Payment.objects.filter(order=order).count(), 2)
        self.assertEqual(CashMovement.objects.filter(related_order=order).count(), 2)

    def test_table_assigned_standalone_is_not_charged_to_open_visit(self):
        order = self.order(100)
        order.table = self.table
        order.save(update_fields=['table'])
        visit = HubVisit.objects.create(table=self.table, created_by=self.admin)
        _page, pay = self.urls(order)
        response = self.client.post(pay, self.payload('standalone:table-only'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.get(order=order).amount_syp, 100)
        self.assertEqual(visit.remaining_syp, 0)

    def test_cancelled_order_and_uncollectible_method_rejected(self):
        order = self.order(100)
        _page, pay = self.urls(order)
        invalid = self.client.post(pay, self.payload('standalone:invalid-method', method='unpaid'))
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(Payment.objects.filter(order=order).exists())
        order.status = Order.Status.CANCELLED
        order.save(update_fields=['status'])
        cancelled = self.client.post(pay, self.payload('standalone:cancelled'))
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(Payment.objects.filter(order=order).exists())

    def test_no_cashier_capability_can_post(self):
        order = self.order(100)
        _page, pay = self.urls(order)
        self.client.force_login(get_user_model().objects.create_user(
            username='unprivileged-standalone', password='x', phone='94129', role='kitchen'))
        response = self.client.post(pay, self.payload('standalone:unauthorized'))
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(order=order).exists())


@override_settings(**WEB)
class StandalonePaymentConcurrencyTests(TransactionTestCase):
    setUp = fixtures.VisitCashierSettlementTests.setUp
    tearDown = fixtures.VisitCashierSettlementTests.tearDown
    order = fixtures.VisitCashierSettlementTests.order

    def run_two(self, order, keys):
        url = reverse('staff_cashier_pay', args=[order.public_code])
        session_cookie = self.client.cookies['sessionid'].value

        def post(key):
            close_old_connections()
            try:
                client = Client()
                client.cookies['sessionid'] = session_cookie
                return client.post(url, {
                    'currency_amount': '100', 'currency_currency': 'SYP_NEW',
                    'method': 'cash', 'notes': 'race', 'idempotency_key': key,
                }).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(post, keys))

    def test_concurrent_same_key_posts_single_cash_and_ledger_entry(self):
        order = self.order(100)
        self.assertEqual(self.run_two(order, ('race:same', 'race:same')), [302, 302])
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=order).count(), 1)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.collect').count(), 1)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), 1)

    def test_concurrent_distinct_full_collections_cannot_overpay(self):
        order = self.order(100)
        statuses = self.run_two(order, ('race:one', 'race:two'))
        self.assertEqual(sorted(statuses), [200, 302])
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=order).count(), 1)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.collect').count(), 1)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), 1)
