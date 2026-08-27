from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import HubVisit, InternetEntitlement, InternetPackage, InternetSession, Room, SystemSetting, TableArea
from core.services.hotspot_connect import hotspot_login_url
from core.services.internet_access import create_entitlement, start_usage_session
from core.services.visits import issue_visit_credential
from core.settings_helpers import get_system_settings


TEST_KEY = Fernet.generate_key().decode()
HOTSPOT_SETTINGS = dict(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
    MIKROTIK_ENABLED=True,
    MIKROTIK_BASE_URL='https://router.test/rest',
    MIKROTIK_USERNAME='api-user',
    MIKROTIK_PASSWORD='api-password',
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_HOTSPOT_LOGIN_URL='https://wifi.test/login',
    MIKROTIK_DEFAULT_PROFILE='hub-default',
    MIKROTIK_USER_PREFIX='hub-',
    MIKROTIK_CREDENTIAL_KEY=TEST_KEY,
)


@override_settings(**HOTSPOT_SETTINGS)
class HotspotOneTapTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 1')
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        self.package = InternetPackage.objects.create(
            name_ar='ساعة إنترنت', code='hotspot-hour', price_syp=500,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60, visible_to_customer=True,
            backend_config={'network_backend': 'mikrotik'},
        )
        self.visit = HubVisit.objects.create(table=self.table)
        self.entitlement = create_entitlement(self.package, visit=self.visit)
        self.assertEqual(self.entitlement.network_backend, 'mikrotik')
        self.session = start_usage_session(self.entitlement, visit=self.visit)
        self.password = 'temporary-hotspot-secret'
        cipher = Fernet(TEST_KEY.encode())
        self.entitlement.network_status = InternetEntitlement.NetworkStatus.PROVISIONED
        self.entitlement.external_network_identifier = 'hub-customer-1'
        self.entitlement.network_credential_encrypted = cipher.encrypt(self.password.encode()).decode()
        self.entitlement.save(update_fields=[
            'network_status', 'external_network_identifier', 'network_credential_encrypted', 'updated_at'
        ])
        _credential, raw_token = issue_visit_credential(self.visit)
        self.client.cookies['hub_visit'] = raw_token
        self.connect_url = reverse(
            'visit_internet_session_connect', kwargs={'public_code': self.session.public_code})

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_package_backend_config_is_snapshotted_on_entitlement(self):
        self.assertEqual(self.entitlement.network_backend, 'mikrotik')
        self.assertEqual(self.session.network_provider, 'mikrotik')

    def test_current_visit_shows_reconnect_for_mikrotik_session(self):
        response = self.client.get(reverse('current_visit'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'إعادة توصيل هذا الجهاز')
        self.assertContains(response, self.connect_url)

    def test_connect_is_post_only_and_visit_scoped(self):
        self.assertEqual(self.client.get(self.connect_url).status_code, 405)
        other = self.client_class()
        response = other.post(self.connect_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('menu_public'))

    def test_connect_returns_no_store_https_post_relay(self):
        response = self.client.post(self.connect_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'action="https://wifi.test/login"')
        self.assertContains(response, 'name="username" value="hub-customer-1"')
        self.assertContains(response, f'name="password" value="{self.password}"')
        self.assertContains(response, 'name="dst"')
        self.assertEqual(response['Cache-Control'], 'no-store, private, max-age=0')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn("form-action https://wifi.test", response['Content-Security-Policy'])
        self.assertNotIn(self.password, response.get('Location', ''))

    @override_settings(MIKROTIK_HOTSPOT_LOGIN_URL='http://wifi.test/login')
    def test_http_hotspot_login_is_rejected_and_button_hidden(self):
        with self.assertRaises(ValidationError):
            hotspot_login_url()
        response = self.client.get(reverse('current_visit'))
        self.assertNotContains(response, 'إعادة توصيل هذا الجهاز')
        relay = self.client.post(self.connect_url)
        self.assertEqual(relay.status_code, 302)
        self.assertEqual(relay['Location'], reverse('current_visit'))

    def test_manual_entitlement_never_gets_connect_button(self):
        manual_package = InternetPackage.objects.create(
            name_ar='يدوي', code='manual-pass', price_syp=100,
            access_mode=InternetPackage.AccessMode.ALLOWANCE,
            total_minutes_limit=30,
            backend_config={'network_backend': 'manual'},
        )
        manual = create_entitlement(manual_package, visit=self.visit)
        manual_session = start_usage_session(manual, visit=self.visit)
        self.assertEqual(manual.network_backend, 'manual')
        response = self.client.get(reverse('current_visit'))
        manual_url = reverse(
            'visit_internet_session_connect', kwargs={'public_code': manual_session.public_code})
        self.assertNotContains(response, manual_url)


class HotspotConfigurationTests(TestCase):
    @override_settings(MIKROTIK_HOTSPOT_LOGIN_URL='https://user:pass@wifi.test/login')
    def test_userinfo_in_login_url_is_rejected(self):
        with self.assertRaises(ValidationError):
            hotspot_login_url()

    @override_settings(MIKROTIK_HOTSPOT_LOGIN_URL='https://wifi.test/login?username=x')
    def test_query_in_login_url_is_rejected(self):
        with self.assertRaises(ValidationError):
            hotspot_login_url()
