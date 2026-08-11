from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from core.models import InternetBandwidthProfile, InternetEntitlement, InternetPackage
from core.services.internet_access import create_entitlement
from core.services.mikrotik import MikroTikProvisioningError
from core.services.network_backends import (ManualNetworkBackend, MikroTikNetworkBackend,
                                             get_network_backend)


class FakeRouterOSClient:
    def __init__(self):
        self.users = {}; self.sessions = []; self.creates = 0; self.updates = 0
    def system_resource(self): return {'version': '7.20'}
    def find_profile(self, name): return {'.id': '*1', 'name': name}
    def find_hotspot_user(self, name): return self.users.get(name)
    def create_hotspot_user(self, values):
        self.creates += 1; self.users[values['name']] = {'.id': '*2', **values}
    def update_hotspot_user(self, remote_id, values):
        self.updates += 1
        for user in self.users.values():
            if user['.id'] == remote_id: user.update(values)
    def active_sessions(self, name): return [row for row in self.sessions if row['user'] == name]
    def remove_active(self, remote_id): self.sessions = [row for row in self.sessions if row['.id'] != remote_id]


SETTINGS = dict(MIKROTIK_ENABLED=True, MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
                MIKROTIK_DEFAULT_PROFILE='hub-default', MIKROTIK_USER_PREFIX='hub-',
                MIKROTIK_CREDENTIAL_KEY='test-key-not-used-by-fake')


@override_settings(**SETTINGS)
class MikroTikBackendTests(TestCase):
    def setUp(self):
        profile = InternetBandwidthProfile.objects.create(code='fast', name='Fast', router_profile_name='hub-fast')
        package = InternetPackage.objects.create(name_ar='رصيد', code='allowance-router', duration_minutes=0,
            price_syp=1, access_mode='allowance', total_minutes_limit=90, bandwidth_profile=profile,
            max_concurrent_devices=2, max_registered_devices=2)
        self.entitlement = create_entitlement(package)
        self.client = FakeRouterOSClient(); self.backend = MikroTikNetworkBackend(client=self.client)
        self.backend._credential = lambda entitlement: ('random-test-only-password', 'encrypted-test-token')

    def test_provision_is_owned_mapped_and_idempotent(self):
        self.entitlement.minutes_used = 10; self.entitlement.save(update_fields=['minutes_used'])
        self.backend.provision_access(self.entitlement)
        user = self.client.users[self.backend.username(self.entitlement)]
        self.assertEqual((user['profile'], user['limit-uptime'], user['shared-users']), ('hub-fast', '1:20:00', '2'))
        self.assertEqual(user['comment'], f'hub-entitlement:{self.entitlement.pk}')
        self.assertNotIn(self.entitlement.access_code, user['name'])
        self.backend.provision_access(self.entitlement)
        self.assertEqual((self.client.creates, self.client.updates), (1, 1))

    def test_refresh_recomputes_remaining_without_reset(self):
        self.backend.provision_access(self.entitlement)
        self.entitlement.minutes_used = 30; self.entitlement.save(update_fields=['minutes_used'])
        self.backend.refresh_access(self.entitlement)
        self.assertEqual(self.client.users[self.backend.username(self.entitlement)]['limit-uptime'], '1:00:00')

    def test_collision_refused_and_commercial_state_preserved(self):
        name = self.backend.username(self.entitlement)
        self.client.users[name] = {'.id': '*9', 'name': name, 'comment': 'provider-owned'}
        with self.assertRaises(MikroTikProvisioningError): self.backend.provision_access(self.entitlement)
        self.entitlement.refresh_from_db()
        self.assertEqual(self.entitlement.status, InternetEntitlement.Status.ACTIVE)
        self.assertEqual(self.entitlement.network_status, InternetEntitlement.NetworkStatus.PROVISION_ERROR)

    def test_disconnect_only_matching_sessions_and_repeats(self):
        self.backend.provision_access(self.entitlement); name = self.backend.username(self.entitlement)
        self.client.sessions = [{'.id': '*a', 'user': name}, {'.id': '*b', 'user': 'other'}]
        self.backend.disconnect_access(self.entitlement); self.backend.disconnect_access(self.entitlement)
        self.assertEqual(self.client.sessions, [{'.id': '*b', 'user': 'other'}])

    def test_expired_and_cancelled_are_rejected_before_router_lookup(self):
        for status in (InternetEntitlement.Status.EXPIRED, InternetEntitlement.Status.CANCELLED):
            self.entitlement.status = status; self.entitlement.save(update_fields=['status'])
            with self.assertRaises(ValidationError): self.backend.provision_access(self.entitlement)
        self.assertEqual(self.client.creates, 0)

    def test_single_registered_mac_is_mapped(self):
        self.entitlement.devices.create(device_mac='AA:BB:CC:DD:EE:FF')
        self.assertEqual(self.backend.plan(self.entitlement)['mac-address'], 'AA:BB:CC:DD:EE:FF')

    def test_health_read(self): self.assertTrue(self.backend.test_connection())


class ManualFallbackTests(TestCase):
    @override_settings(MIKROTIK_ENABLED=False)
    def test_disabled_mikrotik_falls_back_to_unchanged_manual(self):
        self.assertIsInstance(get_network_backend('mikrotik'), ManualNetworkBackend)
