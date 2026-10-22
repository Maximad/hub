"""Cashier disclosure of unfinalized Internet and actual closure collection."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, InternetSession, Order, Payment
from core.tests import test_visit_cashier_settlement as fixtures

WEB = dict(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
           STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@override_settings(**WEB)
class VisitMeteredCloseClarityTests(TestCase):
    setUp = fixtures.VisitCashierSettlementTests.setUp
    tearDown = fixtures.VisitCashierSettlementTests.tearDown
    order = fixtures.VisitCashierSettlementTests.order
    visit = fixtures.VisitCashierSettlementTests.visit

    def active_metered(self, visit):
        started = timezone.now() - timedelta(minutes=31)
        return InternetSession.objects.create(
            session_type=InternetSession.SessionType.INTERNET,
            visit=visit,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            start_time=started, started_at=started,
            rate_per_hour_syp=600, minimum_minutes=30,
            rounding_increment_minutes=15,
            status=InternetSession.Status.ACTIVE,
        )

    def url(self, visit):
        return reverse('staff_cashier_order', args=[visit.public_code])

    def test_full_and_drawer_explain_current_balance_excludes_active_metered_usage(self):
        visit = self.visit()
        self.order(100, visit=visit)
        session = self.active_metered(visit)
        before = (Order.objects.count(), Payment.objects.count())
        for query in ('', '?panel=payment'):
            with self.subTest(query=query):
                response = self.client.get(self.url(visit) + query)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'الرصيد')
                self.assertContains(response, 'ليس المبلغ النهائي')
                self.assertContains(response, 'رسوم')
                self.assertEqual(len(response.context['active_metered_sessions']), 1)
                self.assertEqual(response.context['remaining'], 100)
        session.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual((Order.objects.count(), Payment.objects.count()), before)

    def test_regular_visit_without_metered_internet_does_not_show_unfinalized_warning(self):
        visit = self.visit()
        self.order(100, visit=visit)
        for query in ('', '?panel=payment'):
            response = self.client.get(self.url(visit) + query)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'ليس المبلغ النهائي')
            self.assertEqual(response.context['active_metered_sessions'], [])

    def test_closure_reports_actual_collected_and_new_metered_charge(self):
        visit = self.visit()
        self.order(100, visit=visit)
        session = self.active_metered(visit)
        response = self.client.post(
            reverse('staff_cashier_pay', args=[visit.public_code]),
            {'action': 'settle_close', 'method': Payment.Method.CASH,
             'idempotency_key': 'metered-close-clarity:one'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        visit.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(visit.status, visit.Status.CLOSED)
        self.assertEqual(session.status, InternetSession.Status.BILLED)
        internet_order = Order.objects.get(visit=visit, items__product=self.internet_product)
        due_added = internet_order.total_syp
        self.assertGreater(due_added, 0)
        full_paid = 100 + due_added
        self.assertEqual(sum(p.amount_syp for p in Payment.objects.filter(order__visit=visit)), full_paid)
        self.assertContains(response, f'تم تحصيل {full_paid} ل.س')
        self.assertContains(response, f'يتضمن المبلغ {due_added} ل.س')
        audit = ActivityLog.objects.filter(action='visit.settled_and_closed').latest('pk')
        self.assertEqual(audit.details['recorded_due_before_finalization_syp'], '100')
        self.assertEqual(audit.details['new_metered_due_syp'], str(due_added))
        self.assertEqual(audit.details['amount_syp'], str(full_paid))
        self.assertEqual(visit.remaining_syp, 0)

    def test_without_metered_usage_closure_reports_actual_amount_only(self):
        visit = self.visit()
        self.order(100, visit=visit)
        response = self.client.post(
            reverse('staff_cashier_pay', args=[visit.public_code]),
            {'action': 'settle_close', 'method': Payment.Method.CASH,
             'idempotency_key': 'metered-close-clarity:no-internet'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تم تحصيل 100 ل.س وإغلاق الحساب.')
        self.assertNotContains(response, 'يتضمن المبلغ')
        audit = ActivityLog.objects.filter(action='visit.settled_and_closed').latest('pk')
        self.assertEqual(audit.details['new_metered_due_syp'], '0')
