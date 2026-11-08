"""Regression tests for separate staff HotSpot usernames without RouterOS profile edits."""
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    InternetBandwidthProfile, InternetEntitlement, InternetSession,
    Order, Payment, SystemSetting,
)
from core.services.internet_internal_access import grant_internal_access, revoke_internal_access
from core.services.staff_device_wifi import COOKIE_NAME, SESSION_MARKER
from internet.models import InternetSessionNetworkOperation


class FakeHotspot:
    def __init__(self):
        self.users = {}
        self.profile_calls = []
        self.created = []

    def find_profile(self, name):
        self.profile_calls.append(name)
        return {'.id': '*profile', 'name': name} if name == 'hub-full' else None

    def find_hotspot_user(self, name):
        return self.users.get(name)

    def create_hotspot_user(self, values):
        self.users[values['name']] = {'.id': '*' + str(len(self.users) + 1), **values}
        self.created.append(dict(values))
        return self.users[values['name']]

    def update_hotspot_user(self, remote_id, values):
        for user in self.users.values():
            if user['.id'] == remote_id:
                user.update(values)
                return user
        raise AssertionError('Unknown HotSpot user')

    def active_sessions(self, _name):
        return []

    def remove_active(self, _remote_id):
        raise AssertionError('No active sessions should be removed during login')


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_ENABLED=True,
    MIKROTIK_HOTSPOT_LOGIN_URL='https://wifi.test/login',
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_DEFAULT_PROFILE='hub-full',
    MIKROTIK_USER_PREFIX='hub-',
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class IndependentStaffWifiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='wifi-staff-bridge',
            password='pass',
            phone='+963900009000',
            role=User.Role.WAITER,
        )
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        self.profile = InternetBandwidthProfile.objects.create(
            code='fast', name='Fast', router_profile_name='hub-full', is_active=True,
        )
        self.entitlement = grant_internal_access(
            staff_user=self.user, grant_kind='team', bandwidth_profile=self.profile,
            access_mode='unlimited', validity_value=30, validity_unit='days',
            max_concurrent_devices=2, max_registered_devices=4, actor=self.user,
        )
        self.router = FakeHotspot()
        self.fernet_key = Fernet.generate_key().decode()
        self.config = override_settings(MIKROTIK_CREDENTIAL_KEY=self.fernet_key)
        self.config.enable()
        self.router_patch = patch('internet.session_network_backends.RouterOSClient', return_value=self.router)
        self.router_patch.start()

    def tearDown(self):
        self.router_patch.stop()
        self.config.disable()

    def _client(self):
        client = Client()
        client.force_login(self.user)
        return client

    def _connect(self, client):
        return client.post(reverse('wifi_entry'), {'wifi_action': 'start_staff_wifi'})

    def test_two_browsers_get_independent_hotspot_users_and_no_customer_sale(self):
        first = self._client()
        second = self._client()
        one = self._connect(first)
        two = self._connect(second)
        self.assertEqual(one.status_code, 200)
        self.assertEqual(two.status_code, 200)
        self.assertIn(COOKIE_NAME, one.cookies)
        self.assertIn(COOKIE_NAME, two.cookies)
        self.assertNotEqual(one.cookies[COOKIE_NAME].value, two.cookies[COOKIE_NAME].value)
        self.assertEqual(len(self.router.users), 2)
        self.assertEqual(len({user['name'] for user in self.router.created}), 2)
        self.assertEqual(self.router.profile_calls, ['hub-full', 'hub-full'])
        self.assertTrue(all(user['profile'] == 'hub-full' for user in self.router.created))
        self.assertEqual(InternetSession.objects.filter(notes=SESSION_MARKER).count(), 2)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertNotIn('mac-address', self.router.created[0])
        self.assertNotIn('shared-users', self.router.created[0])

    def test_same_browser_reuses_its_username_without_creating_third_login(self):
        client = self._client()
        first = self._connect(client)
        self.assertEqual(first.status_code, 200)
        second = self._connect(client)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.router.users), 1)
        self.assertEqual(InternetSession.objects.filter(notes=SESSION_MARKER).count(), 1)

    def test_third_browser_fails_closed_without_disrupting_existing_hotspot_users(self):
        first, second, third = self._client(), self._client(), self._client()
        self.assertEqual(self._connect(first).status_code, 200)
        self.assertEqual(self._connect(second).status_code, 200)
        blocked = self._connect(third)
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(len(self.router.users), 2)
        self.assertEqual(InternetSession.objects.filter(notes=SESSION_MARKER).count(), 2)

    def test_grant_revocation_enqueues_session_disconnect_without_financial_rows(self):
        self.assertEqual(self._connect(self._client()).status_code, 200)
        revoke_internal_access(self.entitlement, actor=self.user)
        self.entitlement.refresh_from_db()
        session = InternetSession.objects.get(notes=SESSION_MARKER)
        self.assertEqual(self.entitlement.status, InternetEntitlement.Status.CANCELLED)
        self.assertEqual(session.status, InternetSession.Status.ENDED)
        self.assertTrue(InternetSessionNetworkOperation.objects.filter(
            session=session, operation=InternetSessionNetworkOperation.Operation.DISCONNECT,
        ).exists())
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        # TestCase suppresses transaction.on_commit: live sessions are not touched by test.
        self.assertEqual(len(self.router.users), 1)

    def test_expired_staff_grant_cannot_relay_even_if_browser_session_exists(self):
        client = self._client()
        self.assertEqual(self._connect(client).status_code, 200)
        self.entitlement.valid_until = timezone.now() - timezone.timedelta(seconds=1)
        self.entitlement.save(update_fields=['valid_until', 'updated_at'])
        self.assertEqual(self._connect(client).status_code, 302)
        self.assertEqual(len(self.router.users), 1)
