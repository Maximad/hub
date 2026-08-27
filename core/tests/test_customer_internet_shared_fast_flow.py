from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import (
    Category,
    HubVisit,
    HubVisitBrowserCredential,
    InternetSession,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.services.visit_internet import start_visit_metered_session
from core.settings_helpers import get_system_settings


BASE_WEB_SETTINGS = dict(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)

MIKROTIK_WEB_SETTINGS = {
    **BASE_WEB_SETTINGS,
    'MIKROTIK_ENABLED': True,
    'MIKROTIK_BASE_URL': 'https://router.example.test',
    'MIKROTIK_USERNAME': 'hub-service',
    'MIKROTIK_PASSWORD': 'unused-in-test',
    'MIKROTIK_VERIFY_TLS': True,
    'MIKROTIK_CA_FILE': '',
    'MIKROTIK_CONNECT_TIMEOUT': 1,
    'MIKROTIK_READ_TIMEOUT': 1,
    'MIKROTIK_HOTSPOT_SERVER': 'hub-hotspot',
    'MIKROTIK_HOTSPOT_LOGIN_URL': 'https://wifi.example.test/login',
    'MIKROTIK_DEFAULT_PROFILE': 'hub-slow',
    'MIKROTIK_CUSTOMER_PROFILE_CODE': 'fast',
    'MIKROTIK_USER_PREFIX': 'hub-',
}


class CustomerInternetFlowMixin:
    def make_customer_setup(self):
        category = Category.objects.create(name_ar='خدمات')
        product = Product.objects.create(
            category=category,
            name_ar='إنترنت حسب الوقت',
            price_syp=0,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
            requires_preparation=False,
            visible_on_qr=False,
            orderable_on_qr=False,
            visible_on_pos=False,
            orderable_on_pos=False,
            not_discountable=True,
            track_margin=False,
        )
        settings_obj = SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
            internet_metered_enabled=True,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=30,
            default_rounding_increment_minutes=15,
            default_minimum_charge_syp=0,
            default_free_grace_minutes=0,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=product,
        )
        get_system_settings.cache_clear()
        room = Room.objects.create(name_ar='مشاريب')
        table = TableArea.objects.create(room=room, name_ar='طاولة 1')
        return settings_obj, table


@override_settings(**BASE_WEB_SETTINGS)
class SharedTableVisitTests(CustomerInternetFlowMixin, TestCase):
    def tearDown(self):
        get_system_settings.cache_clear()

    def test_second_browser_reuses_same_open_table_visit_and_session(self):
        _settings, table = self.make_customer_setup()
        url = reverse('visit_internet_start')
        payload = {
            'mode': 'metered',
            'table': str(table.qr_token),
            'next': 'menu',
        }

        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        self.assertIn('hub_visit', first.cookies)

        second_browser = Client()
        second = second_browser.post(url, payload)
        self.assertEqual(second.status_code, 302)
        self.assertIn('hub_visit', second.cookies)

        self.assertEqual(HubVisit.objects.count(), 1)
        visit = HubVisit.objects.get()
        self.assertEqual(visit.table_id, table.pk)
        self.assertEqual(InternetSession.objects.count(), 1)
        self.assertEqual(InternetSession.objects.get().visit_id, visit.pk)
        self.assertEqual(HubVisitBrowserCredential.objects.count(), 2)
        self.assertEqual(
            set(HubVisitBrowserCredential.objects.values_list('visit_id', flat=True)),
            {visit.pk},
        )


@override_settings(**MIKROTIK_WEB_SETTINGS)
class FastCustomerProfileTests(CustomerInternetFlowMixin, TestCase):
    def tearDown(self):
        get_system_settings.cache_clear()

    def test_customer_metered_session_gets_explicit_fast_profile(self):
        _settings, table = self.make_customer_setup()
        visit = HubVisit.objects.create(table=table)
        credential = SimpleNamespace(visit_id=visit.pk)

        session, created = start_visit_metered_session(
            visit=visit,
            credential=credential,
        )

        self.assertTrue(created)
        self.assertEqual(session.network_provider, InternetSession.NetworkProvider.MIKROTIK)
        self.assertEqual(session.bandwidth_profile, 'fast')

    @patch('core.views.visits.build_session_hotspot_login_payload')
    @patch('core.views.visits.prepare_visit_metered_session_network', return_value=True)
    def test_first_start_action_returns_automatic_hotspot_relay(
        self,
        _prepare_network,
        build_payload,
    ):
        _settings, table = self.make_customer_setup()
        build_payload.return_value = {
            'login_url': 'https://wifi.example.test/login',
            'login_origin': 'https://wifi.example.test',
            'username': 'hub-s-customer',
            'password': 'temporary-secret',
            'destination_url': 'https://hub.example.test/menu/',
        }

        response = self.client.post(
            reverse('visit_internet_start'),
            {
                'mode': 'metered',
                'table': str(table.qr_token),
                'next': 'menu',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/hotspot_connect.html')
        self.assertContains(response, 'جارٍ توصيلك بالشبكة')
        self.assertContains(response, 'action="https://wifi.example.test/login"')
        self.assertContains(response, 'name="username" value="hub-s-customer"')
        self.assertIn('hub_visit', response.cookies)
        self.assertEqual(response['Cache-Control'], 'no-store, private, max-age=0')
        session = InternetSession.objects.get()
        self.assertEqual(session.bandwidth_profile, 'fast')
        build_payload.assert_called_once()
