from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    DailyClose,
    FinancialAccount,
    HubVisit,
    InternetSession,
    NotificationEvent,
    Order,
    Shift,
)
from operations.models import BusinessDay, BusinessDayException, StaffDailyCodeReceipt
from operations.services import (
    build_reconciliation,
    close_open_internet_sessions,
    close_safe_visits,
    current_business_date,
    finalize_business_day,
    open_business_day,
    reveal_daily_code,
    start_closing,
    verify_daily_code,
)


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
    BUSINESS_DAY_CUTOFF_HOUR=4,
    MIKROTIK_ENABLED=False,
)
class BusinessDayTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='day-admin', password='pass', email='day-admin@example.com', phone='+963955000001',
        )
        self.cashier = User.objects.create_user(
            username='day-cashier', password='pass', phone='+963955000002', role=User.Role.CASHIER,
        )
        self.waiter = User.objects.create_user(
            username='day-waiter', password='pass', phone='+963955000003', role=User.Role.WAITER,
        )
        self.provider = User.objects.create_user(
            username='day-provider', password='pass', phone='+963955000004', role=User.Role.INTERNET_PROVIDER,
        )

    def _open(self):
        day = open_business_day(actor=self.admin)
        day.refresh_from_db()
        return day

    def test_open_day_issues_encrypted_code_and_notifies_staff_only(self):
        day = self._open()
        code = reveal_daily_code(day, user=self.waiter)

        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        day.refresh_from_db()
        self.assertNotIn(code, day.code_ciphertext)
        self.assertTrue(verify_daily_code(day, code, user=self.waiter))
        receipt = StaffDailyCodeReceipt.objects.get(business_day=day, user=self.waiter)
        self.assertIsNotNone(receipt.notified_at)
        self.assertIsNotNone(receipt.viewed_at)
        self.assertEqual(receipt.use_count, 1)

        event = NotificationEvent.objects.get(title_ar__startswith='رمز الفريق ليوم')
        recipients = set(event.recipients.values_list('user__username', flat=True))
        self.assertEqual(recipients, {'day-admin', 'day-cashier', 'day-waiter'})
        self.assertNotIn(code, event.message_ar)
        self.assertIn('افتح صفحة اليوم التشغيلي', event.message_ar)
        self.assertNotIn('day-provider', recipients)

    def test_regular_staff_can_view_daily_code_workspace_but_provider_cannot(self):
        self._open()
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_close_day'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'رمز الفريق اليومي')
        self.assertNotContains(response, 'صندوق الاستثناءات')

        self.client.force_login(self.provider)
        denied = self.client.get(reverse('staff_close_day'))
        self.assertEqual(denied.status_code, 403)

    def test_manager_can_open_business_day_from_existing_close_day_route(self):
        self.client.force_login(self.cashier)
        response = self.client.post(reverse('staff_close_day'), {'business_day_action': 'open'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('staff_close_day'))
        day = BusinessDay.objects.get()
        self.assertEqual(day.status, BusinessDay.Status.OPEN)
        self.assertEqual(day.business_date, current_business_date())

    def test_reconciliation_detects_open_order_session_and_shift(self):
        day = self._open()
        visit = HubVisit.objects.create()
        Order.objects.create(visit=visit, status=Order.Status.NEW)
        now = timezone.now()
        InternetSession.objects.create(
            visit=visit,
            started_at=now,
            start_time=now,
            billing_mode=InternetSession.BillingMode.FREE,
            status=InternetSession.Status.ACTIVE,
        )
        Shift.objects.create(opened_by=self.cashier, opened_at=now)

        report = build_reconciliation(day)

        self.assertGreaterEqual(report['blocker_count'], 2)
        fingerprints = {item['fingerprint'] for item in report['exceptions']}
        self.assertTrue(any(value.startswith('order:') for value in fingerprints))
        self.assertTrue(any(value.startswith('internet:') for value in fingerprints))
        self.assertTrue(any(value.startswith('shift:') for value in fingerprints))

    def test_close_open_internet_sessions_ends_free_session(self):
        day = self._open()
        now = timezone.now()
        session = InternetSession.objects.create(
            started_at=now,
            start_time=now,
            billing_mode=InternetSession.BillingMode.FREE,
            status=InternetSession.Status.ACTIVE,
        )

        result = close_open_internet_sessions(day, actor=self.admin)

        session.refresh_from_db()
        self.assertEqual(result['closed'], 1)
        self.assertFalse(result['failures'])
        self.assertNotEqual(session.status, InternetSession.Status.ACTIVE)

    def test_close_safe_visits_only_closes_visits_without_live_or_unsettled_work(self):
        day = self._open()
        safe = HubVisit.objects.create()
        unsafe = HubVisit.objects.create()
        Order.objects.create(visit=unsafe, status=Order.Status.NEW)

        result = close_safe_visits(day, actor=self.admin)

        safe.refresh_from_db()
        unsafe.refresh_from_db()
        self.assertEqual(result['closed'], 1)
        self.assertEqual(safe.status, HubVisit.Status.CLOSED)
        self.assertEqual(unsafe.status, HubVisit.Status.OPEN)

    def test_cashbox_shift_requires_finalized_daily_close(self):
        day = self._open()
        now = timezone.now()
        account = FinancialAccount.objects.create(
            code='cash-test', name_ar='صندوق اختبار', account_type=FinancialAccount.AccountType.ASSET,
            is_active=True,
        )
        Shift.objects.create(
            cashbox=account,
            opened_by=self.cashier,
            closed_by=self.cashier,
            opened_at=now,
            closed_at=now + timedelta(minutes=30),
            opening_amount_syp=0,
            counted_amount_syp=0,
        )

        before = build_reconciliation(day)
        self.assertTrue(any(item['fingerprint'] == f'finance:{account.pk}:daily-close' for item in before['exceptions']))

        DailyClose.objects.create(
            account=account,
            business_date=day.business_date,
            status=DailyClose.Status.CLOSED,
            is_finalized=True,
            opening_cash_syp=0,
            actual_cash_counted_syp=0,
        )
        after = build_reconciliation(day)
        self.assertFalse(any(item['fingerprint'] == f'finance:{account.pk}:daily-close' for item in after['exceptions']))

    def test_final_close_is_blocked_until_hard_exceptions_are_resolved(self):
        day = self._open()
        visit = HubVisit.objects.create()
        Order.objects.create(visit=visit, status=Order.Status.NEW)
        start_closing(day, actor=self.admin)

        with self.assertRaises(ValidationError):
            finalize_business_day(day, actor=self.admin)

        day.refresh_from_db()
        self.assertEqual(day.status, BusinessDay.Status.CLOSING)
        self.assertTrue(BusinessDayException.objects.filter(
            business_day=day,
            severity=BusinessDayException.Severity.BLOCKER,
            status=BusinessDayException.Status.OPEN,
        ).exists())

    def test_final_close_expires_daily_code(self):
        day = self._open()
        code = reveal_daily_code(day, user=self.waiter)
        start_closing(day, actor=self.admin)
        finalize_business_day(day, actor=self.admin)

        day.refresh_from_db()
        self.assertEqual(day.status, BusinessDay.Status.CLOSED)
        self.assertEqual(day.code_ciphertext, '')
        self.assertFalse(verify_daily_code(day, code, user=self.waiter))
