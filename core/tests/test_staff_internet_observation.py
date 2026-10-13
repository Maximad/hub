"""Read-only RouterOS evidence for staff internet connections."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    InternetBandwidthProfile, InternetEntitlement, InternetNetworkOperation,
    InternetRevenueShare, InternetSession, Order, Payment,
)
from core.services.internet_internal_access import grant_internal_access
from core.services.network_backends import MikroTikNetworkBackend
from core.services.staff_internet_observation import (
    observation_is_fresh, observe_staff_internet_connections,
)
from internet.models import StaffInternetConnectionSnapshot


class FakeReadOnlyRouter:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.operations = []

    def find_hotspot_user(self, name):
        self.operations.append(('get-user', name))
        return self.users.get(name)

    def active_session_details(self, name):
        self.operations.append(('get-active', name))
        return self.sessions.get(name, [])


@override_settings(
    MIKROTIK_ENABLED=True, MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_USER_PREFIX='hub-', MIKROTIK_DEFAULT_PROFILE='hub-full',
)
class StaffInternetObservationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.person = User.objects.create_user(
            username='observed-staff', phone='+963900111001',
            password='pass', role=User.Role.WAITER,
        )
        self.other = User.objects.create_user(
            username='other-observed-staff', phone='+963900111002',
            password='pass', role=User.Role.BARTENDER,
        )
        self.profile = InternetBandwidthProfile.objects.create(
            code='fast', name='Fast', router_profile_name='hub-full', is_active=True,
        )
        self.grant = self._grant(self.person)
        self.second_grant = self._grant(self.other)
        self.router = FakeReadOnlyRouter()
        for grant in (self.grant, self.second_grant):
            name = MikroTikNetworkBackend.username(grant)
            self.router.users[name] = {
                '.id': '*1', 'name': name,
                'comment': MikroTikNetworkBackend.ownership(grant),
                'disabled': 'false',
            }

    def _grant(self, staff):
        return grant_internal_access(
            staff_user=staff, grant_kind='team',
            bandwidth_profile=self.profile, access_mode='unlimited',
            validity_value=30, validity_unit='days',
            max_concurrent_devices=2, max_registered_devices=3,
        )

    def test_confirmed_connected_devices_and_no_personal_identifiers_persisted(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'.id': '*A', 'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m'},
            {'.id': '*B', 'user': name, 'mac-address': 'AA:BB:CC:DD:EE:02', 'uptime': '3m'},
            {'.id': '*C', 'user': name, 'mac-address': 'AA:BB:CC:DD:EE:02', 'uptime': '5m'},
        ]
        output = observe_staff_internet_connections(limit=5, client=self.router)
        self.assertEqual(output, {'checked': 2, 'observed': 2, 'failed': 0})
        snapshot = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertEqual(snapshot.state, 'observed')
        self.assertEqual(snapshot.active_login_count, 3)
        self.assertEqual(snapshot.distinct_device_count, 2)
        self.assertTrue(observation_is_fresh(snapshot))
        self.assertNotIn('AA:BB:CC:DD:EE', str(snapshot.__dict__))
        self.assertNotIn('*A', str(snapshot.__dict__))
        self.assertFalse(any(method.startswith('post') for method, _ in self.router.operations))
        self.assertEqual(InternetSession.objects.count(), 0)
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          InternetRevenueShare.objects.count()), (0, 0, 0))

    def test_no_active_logins_is_verified_zero_only_when_query_succeeds(self):
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertEqual(snapshot.active_login_count, 0)
        self.assertEqual(snapshot.distinct_device_count, 0)
        self.assertTrue(observation_is_fresh(snapshot))

    def test_missing_mac_is_unknown_distinct_device_count(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [{'.id': '*A', 'user': name}]
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertEqual(snapshot.active_login_count, 1)
        self.assertIsNone(snapshot.distinct_device_count)

    def test_router_error_and_unowned_resource_never_claim_zero_online(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.users[name]['comment'] = 'external-user'
        result = observe_staff_internet_connections(limit=5, client=self.router)
        self.assertEqual(result['failed'], 1)
        snapshot = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertEqual(snapshot.state, 'error')
        self.assertIsNone(snapshot.active_login_count)
        self.assertEqual(snapshot.error_code, 'observation_failed')
        self.assertFalse(observation_is_fresh(snapshot))
        self.assertNotIn(('get-active', name), self.router.operations)

    def test_missing_provisioned_user_is_not_reported_as_offline(self):
        name = MikroTikNetworkBackend.username(self.grant)
        del self.router.users[name]
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertEqual(snapshot.state, 'missing')
        self.assertIsNone(snapshot.active_login_count)
        self.assertFalse(observation_is_fresh(snapshot))

    def test_oldest_first_polls_bounded_batch_and_rotates(self):
        first = observe_staff_internet_connections(limit=1, client=self.router)
        self.assertEqual(first['checked'], 1)
        self.assertEqual(StaffInternetConnectionSnapshot.objects.count(), 1)
        next_run = observe_staff_internet_connections(limit=1, client=self.router)
        self.assertEqual(next_run['checked'], 1)
        self.assertEqual(StaffInternetConnectionSnapshot.objects.count(), 2)

    def test_stale_status_cannot_be_presented_as_live_online(self):
        observe_staff_internet_connections(limit=5, client=self.router)
        snap = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertFalse(observation_is_fresh(snap, at=snap.checked_at + timedelta(minutes=4)))

    def test_inactive_or_cancelled_staff_are_not_polled(self):
        get_user_model().objects.filter(pk=self.person.pk).update(is_active=False)
        InternetEntitlement.objects.filter(pk=self.second_grant.pk).update(
            status=InternetEntitlement.Status.CANCELLED,
        )
        output = observe_staff_internet_connections(limit=5, client=self.router)
        self.assertEqual(output['checked'], 0)
        self.assertEqual(StaffInternetConnectionSnapshot.objects.count(), 0)

    @override_settings(MIKROTIK_ENABLED=False)
    def test_disabled_router_integration_makes_no_observation(self):
        output = observe_staff_internet_connections(client=self.router)
        self.assertEqual(output['disabled'], True)
        self.assertEqual(StaffInternetConnectionSnapshot.objects.count(), 0)

    def test_invalid_router_results_do_not_count_as_online(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [{'.id': '*A', 'user': 'someone-else',
                                       'mac-address': 'AA:BB:CC:DD:EE:01'}]
        result = observe_staff_internet_connections(limit=5, client=self.router)
        self.assertEqual(result['failed'], 1)
        snap = StaffInternetConnectionSnapshot.objects.get(
            grant=self.grant.internal_staff_grant,
        )
        self.assertIsNone(snap.active_login_count)
