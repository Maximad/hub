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
    observed_concurrent_limit_state, parse_routeros_uptime, sampled_continuity_seconds,
)
from internet.models import (
    StaffInternetConnectionSnapshot, StaffInternetDeviceEvidence,
    StaffInternetObservedInterval,
)


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
        self.assertEqual(snapshot.longest_active_uptime_seconds, 300)
        self.assertEqual(observed_concurrent_limit_state(self.grant, snapshot), 'within_observed_limit')
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
        self.assertEqual(snapshot.longest_active_uptime_seconds, 0)
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

    def test_distinct_devices_over_limit_detected_without_router_mutation(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'.id': '*1', 'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '35s'},
            {'.id': '*2', 'user': name, 'mac-address': 'AA:BB:CC:DD:EE:02', 'uptime': '1m5s'},
            {'.id': '*3', 'user': name, 'mac-address': 'AA:BB:CC:DD:EE:03', 'uptime': '2m'},
        ]
        result = observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertEqual(observed_concurrent_limit_state(self.grant, snapshot), 'exceeded')
        self.assertEqual(snapshot.distinct_device_count, 3)
        self.assertEqual(snapshot.longest_active_uptime_seconds, 120)
        self.assertEqual(result['observed'], 2)
        self.assertTrue(all(action in ('get-user', 'get-active') for action, _ in self.router.operations))
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.status, InternetEntitlement.Status.ACTIVE)

    def test_duplicate_logins_same_mac_do_not_trigger_device_excess(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '59s'},
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m1s'},
        ]
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertEqual(snapshot.active_login_count, 2)
        self.assertEqual(snapshot.distinct_device_count, 1)
        self.assertEqual(observed_concurrent_limit_state(self.grant, snapshot), 'within_observed_limit')

    def test_missing_mac_and_old_observations_never_imply_compliance(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [{'user': name, 'uptime': '10s'}]
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertEqual(observed_concurrent_limit_state(self.grant, snapshot), 'unknown')
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '10s'},
        ]
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot.refresh_from_db()
        self.assertEqual(observed_concurrent_limit_state(
            self.grant, snapshot, at=snapshot.checked_at + timedelta(minutes=4)), 'unknown')

    def test_missing_uptime_prevents_false_longest_session(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m20s'},
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:02'},
        ]
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertIsNone(snapshot.longest_active_uptime_seconds)
        self.assertEqual(snapshot.distinct_device_count, 2)

    def test_supported_uptime_formats_and_bad_input(self):
        for value, expected in (
            ('0s', 0), ('59s', 59), ('1m4s', 64), ('2h3m4s', 7384),
            ('1d00:00:05', 86405), ('1:02:03', 3723),
            ('1w2d3h4m5s', 788645), ('48:00:00', 172800),
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_routeros_uptime(value), expected)
        for value in ('', None, 12, '1h0h', '3x', '0:99:01',
                      '2d24:00:00', 'forever', '400d'):
            with self.subTest(value=value):
                self.assertIsNone(parse_routeros_uptime(value))

    def test_failed_observation_clears_old_duration_and_verdict(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m'}
        ]
        observe_staff_internet_connections(limit=5, client=self.router)
        self.router.users[name]['comment'] = 'not-hub'
        observe_staff_internet_connections(limit=5, client=self.router)
        snapshot = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertIsNone(snapshot.longest_active_uptime_seconds)
        self.assertIsNone(snapshot.distinct_device_count)
        self.assertEqual(observed_concurrent_limit_state(self.grant, snapshot), 'unknown')

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

    def test_pseudonymous_device_history_is_grant_scoped_and_deduplicated(self):
        name = MikroTikNetworkBackend.username(self.grant)
        second = MikroTikNetworkBackend.username(self.second_grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '60s'},
            {'user': name, 'mac-address': 'aa:bb:cc:dd:ee:01', 'uptime': '62s'},
        ]
        self.router.sessions[second] = [
            {'user': second, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '20s'},
        ]
        observe_staff_internet_connections(client=self.router)
        first = StaffInternetDeviceEvidence.objects.get(grant=self.grant.internal_staff_grant)
        other = StaffInternetDeviceEvidence.objects.get(grant=self.second_grant.internal_staff_grant)
        self.assertEqual(StaffInternetDeviceEvidence.objects.count(), 2)
        self.assertEqual(first.last_active_uptime_seconds, 62)
        self.assertNotEqual(first.device_token, other.device_token)
        self.assertEqual(len(first.device_token), 64)
        self.assertNotIn('AA:BB:CC:DD:EE:01', str(first.__dict__))
        self.assertNotIn('*A', str(first.__dict__))
        self.assertEqual(InternetSession.objects.count(), 0)
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          InternetRevenueShare.objects.count()), (0, 0, 0))

    def test_subsequent_poll_updates_last_seen_but_does_not_create_new_device(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '60s'},
        ]
        at = timezone.now()
        observe_staff_internet_connections(client=self.router, at=at)
        evidence = StaffInternetDeviceEvidence.objects.get(grant=self.grant.internal_staff_grant)
        seen_first = evidence.first_seen_at
        self.router.sessions[name][0]['uptime'] = '121s'
        observe_staff_internet_connections(client=self.router, at=at + timedelta(seconds=61))
        evidence.refresh_from_db()
        self.assertEqual(evidence.first_seen_at, seen_first)
        self.assertEqual(evidence.last_seen_at, at + timedelta(seconds=61))
        self.assertEqual(evidence.last_active_uptime_seconds, 121)
        self.assertEqual(StaffInternetDeviceEvidence.objects.filter(grant=self.grant.internal_staff_grant).count(), 1)

    def test_missing_or_malformed_mac_does_not_write_partial_identity_history(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m'},
            {'user': name, 'mac-address': 'not-a-mac', 'uptime': '1m'},
        ]
        observe_staff_internet_connections(client=self.router)
        snap = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertEqual(snap.active_login_count, 2)
        self.assertIsNone(snap.distinct_device_count)
        self.assertFalse(StaffInternetDeviceEvidence.objects.filter(grant=self.grant.internal_staff_grant).exists())
        self.assertFalse(StaffInternetDeviceEvidence.objects.exists())

    def test_failed_observation_never_updates_last_seen(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        observed = StaffInternetDeviceEvidence.objects.get(grant=self.grant.internal_staff_grant)
        self.router.users[name]['comment'] = 'not-ours'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=60))
        observed.refresh_from_db()
        self.assertEqual(observed.last_seen_at, first)
        snap = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertEqual(snap.state, 'error')

    def test_device_records_expire_after_thirty_days_without_new_login(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        self.assertEqual(StaffInternetDeviceEvidence.objects.count(), 1)
        self.router.sessions[name] = []
        observe_staff_internet_connections(client=self.router, at=first + timedelta(days=29))
        self.assertEqual(StaffInternetDeviceEvidence.objects.count(), 1)
        observe_staff_internet_connections(client=self.router, at=first + timedelta(days=31))
        self.assertEqual(StaffInternetDeviceEvidence.objects.count(), 0)

    @override_settings(MIKROTIK_ENABLED=False)
    def test_disabled_network_integration_never_records_new_device_evidence(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '1m'},
        ]
        result = observe_staff_internet_connections(client=self.router)
        self.assertTrue(result['disabled'])
        self.assertFalse(StaffInternetDeviceEvidence.objects.exists())


    def test_adjacent_observations_append_durable_interval_once(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '60s'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        self.assertFalse(StaffInternetObservedInterval.objects.exists())
        self.router.sessions[name][0]['uptime'] = '121s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=61))
        interval = StaffInternetObservedInterval.objects.get()
        self.assertEqual(interval.observed_seconds, 61)
        self.assertEqual(interval.start_at, first)
        self.assertEqual(interval.end_at, first + timedelta(seconds=61))
        self.assertEqual(StaffInternetDeviceEvidence.objects.count(), 1)
        # Idempotency includes repeat polls and stale out-of-order observations.
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=61))
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=20))
        self.assertEqual(StaffInternetObservedInterval.objects.count(), 1)
        snap = StaffInternetConnectionSnapshot.objects.get(grant=self.grant.internal_staff_grant)
        self.assertEqual(snap.checked_at, first + timedelta(seconds=61))
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          InternetRevenueShare.objects.count()), (0, 0, 0))

    def test_restart_or_reconnect_does_not_backfill_missing_time(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '300s'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        # Simulated RouterOS restart or a new login with lower uptime.
        self.router.sessions[name][0]['uptime'] = '10s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=60))
        self.assertFalse(StaffInternetObservedInterval.objects.exists())
        self.router.sessions[name][0]['uptime'] = '70s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=120))
        intervals = list(StaffInternetObservedInterval.objects.all())
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].observed_seconds, 60)
        self.assertEqual(intervals[0].start_at, first + timedelta(seconds=60))

    def test_missing_poll_does_not_credit_long_unobserved_gap(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '100s'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        self.router.sessions[name][0]['uptime'] = '400s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=5))
        self.assertFalse(StaffInternetObservedInterval.objects.exists())
        self.router.sessions[name][0]['uptime'] = '460s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=6))
        self.assertEqual(StaffInternetObservedInterval.objects.count(), 1)
        self.assertEqual(StaffInternetObservedInterval.objects.get().observed_seconds, 60)

    def test_failed_or_incomplete_sample_breaks_continuity(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '100s'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        self.router.users[name]['comment'] = 'foreign-ownership'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=1))
        self.router.users[name]['comment'] = MikroTikNetworkBackend.ownership(self.grant)
        self.router.sessions[name][0]['uptime'] = '220s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=2))
        self.assertFalse(StaffInternetObservedInterval.objects.exists())
        self.router.sessions[name][0]['uptime'] = '280s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=3))
        self.assertEqual(StaffInternetObservedInterval.objects.count(), 1)
        self.router.sessions[name][0].pop('uptime')
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=4))
        self.router.sessions[name][0]['uptime'] = '400s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(minutes=5))
        self.assertEqual(StaffInternetObservedInterval.objects.count(), 1)

    def test_two_devices_have_separate_rows_without_duplicate_login_inflation(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '100s'},
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '95s'},
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:02', 'uptime': '50s'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        self.router.sessions[name][0]['uptime'] = '160s'
        self.router.sessions[name][1]['uptime'] = '155s'
        self.router.sessions[name][2]['uptime'] = '110s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=60))
        self.assertEqual(StaffInternetObservedInterval.objects.count(), 2)
        self.assertEqual(sum(StaffInternetObservedInterval.objects.values_list(
            'observed_seconds', flat=True)), 120)
        self.assertEqual(StaffInternetDeviceEvidence.objects.filter(
            grant=self.grant.internal_staff_grant).count(), 2)

    def test_30_day_prune_removes_device_and_interval_history(self):
        name = MikroTikNetworkBackend.username(self.grant)
        self.router.sessions[name] = [
            {'user': name, 'mac-address': 'AA:BB:CC:DD:EE:01', 'uptime': '10s'},
        ]
        first = timezone.now()
        observe_staff_internet_connections(client=self.router, at=first)
        self.router.sessions[name][0]['uptime'] = '70s'
        observe_staff_internet_connections(client=self.router, at=first + timedelta(seconds=60))
        self.assertEqual(StaffInternetObservedInterval.objects.count(), 1)
        self.router.sessions[name] = []
        observe_staff_internet_connections(client=self.router, at=first + timedelta(days=31))
        self.assertFalse(StaffInternetDeviceEvidence.objects.exists())
        self.assertFalse(StaffInternetObservedInterval.objects.exists())

    def test_interval_inference_boundaries(self):
        first = timezone.now()
        last = first + timedelta(seconds=60)
        self.assertEqual(sampled_continuity_seconds(first, last, 100, 161), 60)
        self.assertIsNone(sampled_continuity_seconds(first, first, 100, 101))
        self.assertIsNone(sampled_continuity_seconds(last, first, 100, 161))
        self.assertIsNone(sampled_continuity_seconds(first, last, 100, None))
        self.assertIsNone(sampled_continuity_seconds(first, last, 100, 105))
        self.assertIsNone(sampled_continuity_seconds(first, last, 100, 95))
        self.assertIsNone(sampled_continuity_seconds(first, last, 100, 180))
        self.assertIsNone(sampled_continuity_seconds(
            first, first + timedelta(seconds=121), 100, 221))
