import hashlib
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (HubVisit, HubVisitBrowserCredential, InternetEntitlement,
    InternetPackage, InternetRevenueShare, InternetSession, Order, Payment, Room,
    SystemSetting, TableArea)
from core.services.internet_access import (create_commercial_sale, end_usage_session,
                                           start_usage_session)
from core.settings_helpers import get_system_settings


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False, STORAGES={
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class VisitInternetSelfServiceTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 1')
        self.settings = SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        self.package = InternetPackage.objects.create(
            name_ar='ساعة إنترنت', code='visit-hour', price_syp=4000,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60, visible_to_customer=True,
        )
        self.menu_url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})

    def tearDown(self):
        get_system_settings.cache_clear()

    def payload(self, package=None, key='strong-browser-request'):
        return {'package': str((package or self.package).public_code),
                'table': str(self.table.qr_token), 'request_key': key}

    def test_both_flags_are_required_and_hidden_package_is_rejected(self):
        self.settings.customer_internet_self_service_enabled = False
        self.settings.save(update_fields=['customer_internet_self_service_enabled', 'updated_at'])
        get_system_settings.cache_clear()
        self.assertNotContains(self.client.get(self.menu_url), 'ابدأ الآن')
        self.assertFalse(HubVisit.objects.exists())
        self.assertRedirects(self.client.post(reverse('visit_internet_start'), self.payload()), reverse('menu_public'))
        self.settings.customer_internet_self_service_enabled = True
        self.settings.save(update_fields=['customer_internet_self_service_enabled', 'updated_at'])
        self.package.visible_to_customer = False
        self.package.save(update_fields=['visible_to_customer', 'updated_at'])
        get_system_settings.cache_clear()
        self.client.post(reverse('visit_internet_start'), self.payload())
        self.assertFalse(HubVisit.objects.exists())
        self.assertFalse(InternetEntitlement.objects.exists())

    def test_internet_first_action_creates_atomic_unpaid_visit_sale_and_session(self):
        response = self.client.post(reverse('visit_internet_start'), self.payload())
        self.assertRedirects(response, reverse('current_visit'))
        visit = HubVisit.objects.get()
        credential = HubVisitBrowserCredential.objects.get()
        entitlement = InternetEntitlement.objects.get()
        order = Order.objects.get()
        session = InternetSession.objects.get()
        self.assertEqual(credential.visit, visit)
        self.assertEqual(entitlement.visit, visit)
        self.assertEqual(order.visit, visit)
        self.assertEqual(session.visit, visit)
        self.assertEqual(entitlement.order, order)
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(visit.remaining_syp, 4000)
        self.assertEqual(credential.token_hash,
                         hashlib.sha256(response.cookies['hub_visit'].value.encode()).hexdigest())

    def test_retry_reuses_sale_and_active_session(self):
        first = self.client.post(reverse('visit_internet_start'), self.payload())
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value
        self.client.post(reverse('visit_internet_start'), self.payload())
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(InternetEntitlement.objects.count(), 1)
        self.assertEqual(InternetSession.objects.count(), 1)
        self.assertLessEqual(InternetRevenueShare.objects.count(), 1)

    def test_other_browser_cannot_control_visit_entitlement(self):
        first = self.client.post(reverse('visit_internet_start'), self.payload())
        entitlement = InternetEntitlement.objects.get()
        other = self.client_class()
        response = other.post(reverse('visit_internet_entitlement_start',
                                      kwargs={'public_code': entitlement.public_code}))
        self.assertRedirects(response, reverse('menu_public'))
        self.assertEqual(InternetSession.objects.count(), 1)


class InternetSessionDomainRegressionTests(TestCase):
    def test_timed_entitlement_is_one_shot_but_manual_calls_need_no_visit(self):
        package = InternetPackage.objects.create(
            name_ar='ساعة', code='one-shot-regression', price_syp=1000,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60,
        )
        entitlement = create_commercial_sale(
            package, payment_method=Payment.Method.UNPAID,
            idempotency_key='manual-regression-sale',
        )
        session = start_usage_session(entitlement)
        self.assertIsNone(session.visit)
        end_usage_session(session, at=timezone.now() + timedelta(minutes=10))
        with self.assertRaises(ValidationError):
            start_usage_session(entitlement)

    def test_allowance_can_stop_and_restart_without_visit(self):
        package = InternetPackage.objects.create(
            name_ar='100 دقيقة', code='allowance-regression', price_syp=1000,
            access_mode=InternetPackage.AccessMode.ALLOWANCE,
            total_minutes_limit=100,
        )
        entitlement = create_commercial_sale(
            package, payment_method=Payment.Method.UNPAID,
            idempotency_key='manual-allowance-regression',
        )
        started = timezone.now()
        first = start_usage_session(entitlement, at=started)
        end_usage_session(first, at=started + timedelta(minutes=12))
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.minutes_remaining, 88)
        second = start_usage_session(entitlement, at=started + timedelta(minutes=13))
        self.assertIsNone(second.visit)
