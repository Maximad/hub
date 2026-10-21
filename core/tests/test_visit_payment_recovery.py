"""Cashier recovery preserves the original payment intent and atomic evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (ActivityLog, CashMovement, CurrencyEntrySnapshot, HubVisit,
    InternetSession, NotificationEvent, Order, Payment, PostingBatch, PostingCommand, PostingEntry)
from core.services.posting.context import PostingContext
from core.services.visit_settlement import allocate_visit_payment
from core.services.visits import issue_visit_credential
from core.tests import test_visit_cashier_settlement as fixtures
from internet.models import InternetSessionNetworkOperation

WEB = dict(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False, MIKROTIK_ENABLED=False,
           STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
MODELS = (Payment, CashMovement, PostingBatch, PostingEntry, PostingCommand, CurrencyEntrySnapshot)


@override_settings(**WEB)
class VisitPaymentRecoveryTests(TestCase):
    setUp = fixtures.VisitCashierSettlementTests.setUp
    tearDown = fixtures.VisitCashierSettlementTests.tearDown
    visit = fixtures.VisitCashierSettlementTests.visit
    order = fixtures.VisitCashierSettlementTests.order

    def account(self):
        visit = self.visit()
        self.order(100, visit=visit)
        self.order(200, visit=visit)
        return visit

    def counts(self):
        return tuple(model.objects.count() for model in MODELS)

    def payload(self, key='recovery-pay', amount='150', **extra):
        return {'idempotency_key': key, 'currency_amount': amount, 'currency_currency': 'SYP_NEW',
                'method': 'cash', 'notes': 'دفعة تجريبية', **extra}

    def pay_url(self, visit, panel=False):
        return reverse('staff_cashier_pay', args=[visit.public_code]) + ('?panel=payment' if panel else '')

    def test_both_surfaces_supply_keys_and_default_amount_without_javascript(self):
        visit = self.account()
        url = reverse('staff_cashier_order', args=[visit.public_code])
        for suffix in ('', '?panel=payment'):
            page = self.client.get(url + suffix)
            for name in ('collection_request_key', 'settlement_request_key'):
                self.assertContains(page, 'name="idempotency_key" value="' + page.context[name] + '"')
            self.assertContains(page, 'value="300" required data-currency-amount')
            self.assertNotEqual(page.context['collection_request_key'], page.context['settlement_request_key'])

    def test_validation_preserves_safe_fields_and_key_but_never_manager_credentials(self):
        visit = self.account()
        before = self.counts()
        payload = self.payload(amount='999', method='manual_transfer', manager_username='PRIVATE-MANAGER',
                               manager_password='PRIVATE-PASSWORD', notes='<ملاحظة>')
        for panel in (False, True):
            page = self.client.post(self.pay_url(visit, panel), payload)
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, 'المبلغ لا يجوز أن يتجاوز')
            self.assertEqual(page.context['collection_request_key'], payload['idempotency_key'])
            self.assertEqual(page.context['form_values']['currency_amount'], '999')
            self.assertContains(page, 'value="manual_transfer" selected')
            self.assertContains(page, '&lt;ملاحظة&gt;')
            self.assertNotContains(page, 'PRIVATE-MANAGER')
            self.assertNotContains(page, 'PRIVATE-PASSWORD')
        self.assertEqual(self.counts(), before)

    def test_snapshot_failure_rolls_back_all_money_and_recovers_same_form(self):
        visit = self.account()
        for panel in (False, True):
            before = self.counts()
            with patch('core.views.staff_cashier_visits.CurrencyEntryFormService.snapshot', side_effect=RuntimeError('PRIVATE snapshot error')), self.assertLogs('core.views.staff_cashier_visits', level='ERROR'):
                page = self.client.post(self.pay_url(visit, panel), self.payload())
            self.assertContains(page, 'تعذر تأكيد حفظ الدفعة.')
            self.assertNotContains(page, 'PRIVATE snapshot error')
            self.assertEqual(page.context['collection_request_key'], 'recovery-pay')
            self.assertEqual(self.counts(), before)
            self.assertEqual(visit.remaining_syp, 300)
        success = self.client.post(self.pay_url(visit, True), self.payload())
        self.assertEqual(success.status_code, 200)
        self.assertEqual(visit.remaining_syp, 150)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(CashMovement.objects.count(), 2)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), 1)
        self.assertNotEqual(success.context['collection_request_key'], 'recovery-pay')

    def test_validation_failure_after_posting_also_rolls_back(self):
        visit = self.account()
        before = self.counts()
        with patch('core.views.staff_cashier_visits.CurrencyEntryFormService.snapshot', side_effect=ValidationError('تعذر توثيق العملة.')):
            page = self.client.post(self.pay_url(visit), self.payload())
        self.assertContains(page, 'تعذر توثيق العملة.')
        self.assertEqual(self.counts(), before)
        self.assertFalse(ActivityLog.objects.filter(action='visit_payment_allocated').exists())

    def test_late_partial_approval_audit_failure_rolls_back_snapshot_and_postings(self):
        visit = self.account()
        before = self.counts()
        create = ActivityLog.objects.create
        def audit(*args, **kwargs):
            if kwargs.get('action') == 'visit_partial_payment_approved':
                raise RuntimeError('PRIVATE late audit error')
            return create(*args, **kwargs)
        with patch('core.views.staff_cashier_visits.ActivityLog.objects.create', side_effect=audit), self.assertLogs('core.views.staff_cashier_visits', level='ERROR'):
            page = self.client.post(self.pay_url(visit), self.payload())
        self.assertContains(page, 'تعذر تأكيد حفظ الدفعة.')
        self.assertEqual(self.counts(), before)
        self.assertEqual(visit.remaining_syp, 300)

    def test_fully_paid_retry_is_successful_without_rechecking_rates_or_new_evidence(self):
        visit = self.account()
        payload = self.payload(amount='300')
        self.client.post(self.pay_url(visit), payload)
        before = self.counts()
        with patch('core.views.staff_cashier_visits.CurrencyEntryFormService.clean', side_effect=AssertionError('A replay must not reinterpret money')):
            page = self.client.post(self.pay_url(visit, True), payload)
        self.assertContains(page, 'هذه الدفعة مسجلة بالفعل.')
        self.assertEqual(self.counts(), before)
        self.assertEqual(visit.remaining_syp, 0)
        # A fresh form remains usable to close the zero-balance visit.
        self.client.post(self.pay_url(visit), {'action': 'settle_close', 'idempotency_key': 'close-paid'})
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'closed')
        again = self.client.post(self.pay_url(visit, True), payload)
        self.assertContains(again, 'هذه الدفعة مسجلة بالفعل.')
        self.assertEqual(self.counts(), before)

    def test_changed_intent_foreign_account_and_other_actor_cannot_reuse_receipt(self):
        visit = self.account()
        payload = self.payload()
        self.client.post(self.pay_url(visit), payload)
        before = self.counts()
        for changes in ({'currency_amount': '100'}, {'method': 'manual_transfer'}, {'notes': 'تعديل'}):
            page = self.client.post(self.pay_url(visit, True), {**payload, **changes})
            self.assertContains(page, 'تغيّرت تفاصيل طلب الدفع')
        foreign = self.account()
        page = self.client.post(self.pay_url(foreign, True), payload)
        self.assertContains(page, 'مرجع الدفع مستخدم لعملية أخرى')
        self.client.force_login(self.cashier)
        page = self.client.post(self.pay_url(visit, True), payload)
        self.assertContains(page, 'تغيّرت تفاصيل طلب الدفع أو الموظف')
        self.assertEqual(self.counts(), before)
        self.assertEqual(foreign.remaining_syp, 300)

    def test_fresh_forms_allow_two_legitimate_equal_payments(self):
        visit = self.account()
        url = reverse('staff_cashier_order', args=[visit.public_code])
        first_key = self.client.get(url).context['collection_request_key']
        self.client.post(self.pay_url(visit), self.payload(first_key, '100'))
        second_key = self.client.get(url).context['collection_request_key']
        self.assertNotEqual(first_key, second_key)
        self.client.post(self.pay_url(visit), self.payload(second_key, '100'))
        self.assertEqual(visit.remaining_syp, 100)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), 2)

    def test_partial_manager_approval_and_notification_are_preserved(self):
        visit = self.account()
        self.client.force_login(self.cashier)
        payload = self.payload()
        denied = self.client.post(self.pay_url(visit, True), payload)
        self.assertContains(denied, 'الدفع الجزئي يحتاج موافقة مدير.')
        self.assertEqual(Payment.objects.count(), 0)
        self.assertTrue(NotificationEvent.objects.filter(event_type='manager_approval_needed').exists())
        self.assertEqual(denied.context['collection_request_key'], payload['idempotency_key'])
        approved = self.client.post(self.pay_url(visit, True), {**payload,
            'manager_username': self.admin.username, 'manager_password': 'adminpass'})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(visit.remaining_syp, 150)

    def test_permissions_revocation_post_only_and_csrf_prevent_writes(self):
        visit = self.account()
        before = self.counts()
        self.assertEqual(self.client.get(self.pay_url(visit)).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(self.pay_url(visit), self.payload()).status_code, 403)
        UserCapabilityOverride.objects.create(user=self.admin, capability='cashier', allowed=False)
        self.client.post(self.pay_url(visit), self.payload())
        self.assertEqual(self.counts(), before)

    def test_service_receipts_are_scoped_to_source_and_immutable_payment_details(self):
        visit = self.account()
        context = PostingContext(actor=self.admin, idempotency_key='service-replay')
        allocate_visit_payment(visit, context, 100, 'cash')
        before = self.counts()
        for target, amount, method in ((visit, 150, 'cash'), (visit, 100, 'manual_transfer'),
                                      (self.account(), 100, 'cash')):
            with self.assertRaises(ValidationError):
                allocate_visit_payment(target, context, amount, method)
        self.assertEqual(self.counts(), before)

    def test_late_settlement_failure_restores_internet_credentials_and_money(self):
        visit = self.account()
        credential, _ = issue_visit_credential(visit)
        started = timezone.now() - timedelta(minutes=31)
        session = InternetSession.objects.create(visit=visit, start_time=started, started_at=started,
            billing_mode='open_metered', rate_per_hour_syp=600, minimum_minutes=30)
        before = self.counts()
        order_count = Order.objects.count()
        jobs = InternetSessionNetworkOperation.objects.count()
        payload = {'action': 'settle_close', 'idempotency_key': 'settle-recovery', 'method': 'cash', 'notes': 'إغلاق تجريبي'}
        create = ActivityLog.objects.create
        def audit(*args, **kwargs):
            if kwargs.get('action') == 'visit.settled_and_closed':
                raise RuntimeError('PRIVATE settlement error')
            return create(*args, **kwargs)
        with patch('core.views.staff_cashier_visits.ActivityLog.objects.create', side_effect=audit), self.assertLogs('core.views.staff_cashier_visits', level='ERROR'):
            failed = self.client.post(self.pay_url(visit, True), payload)
        self.assertContains(failed, 'تعذر تأكيد تسديد وإغلاق الحساب.')
        self.assertNotContains(failed, 'PRIVATE settlement error')
        self.assertEqual(failed.context['settlement_request_key'], 'settle-recovery')
        self.assertEqual(failed.context['settlement_values']['notes'], payload['notes'])
        self.assertEqual(self.counts(), before)
        self.assertEqual(Order.objects.count(), order_count)
        self.assertEqual(InternetSessionNetworkOperation.objects.count(), jobs)
        visit.refresh_from_db(); session.refresh_from_db(); credential.refresh_from_db()
        self.assertEqual(visit.status, 'open')
        self.assertEqual(session.status, 'active')
        self.assertIsNone(credential.revoked_at)
        self.client.post(self.pay_url(visit), payload)
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'closed')
        settled = self.counts()
        self.client.post(self.pay_url(visit), payload)
        self.assertEqual(self.counts(), settled)


@override_settings(**WEB)
class VisitPaymentRecoveryConcurrencyTests(TransactionTestCase):
    setUp = fixtures.VisitCashierSettlementTests.setUp
    tearDown = fixtures.VisitCashierSettlementTests.tearDown
    visit = fixtures.VisitCashierSettlementTests.visit
    order = fixtures.VisitCashierSettlementTests.order

    def test_concurrent_full_payments_and_replay_are_exactly_once(self):
        visit = self.visit()
        self.order(300, visit=visit)
        url = reverse('staff_cashier_pay', args=[visit.public_code])
        def pay(key):
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.admin)
                return client.post(url, {'currency_amount': '300', 'method': 'cash', 'idempotency_key': key}).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(pay, ['concurrent-pay', 'concurrent-pay'])), [302, 302])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashMovement.objects.count(), 1)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), 1)
        fresh = self.visit()
        self.order(300, visit=fresh)
        url = reverse('staff_cashier_pay', args=[fresh.public_code])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(pay, ['different-1', 'different-2']))
        self.assertEqual(sorted(results), [200, 302])
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(CashMovement.objects.count(), 2)
        self.assertEqual(CurrencyEntrySnapshot.objects.count(), 2)
