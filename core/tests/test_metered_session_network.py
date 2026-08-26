from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import InternetBandwidthProfile, InternetSession
from core.services.mikrotik import MikroTikProvisioningError
from internet.models import InternetSessionNetworkOperation, InternetSessionNetworkState
from internet.session_network_backends import (
    DISCONNECTED,
    PROVISIONED,
    PROVISION_ERROR,
    ManualSessionNetworkBackend,
    MikroTikSessionNetworkBackend,
)
from internet.session_network_operations import (
    enqueue_session_network_operation,
    process_session_network_operation,
)


TEST_KEY = Fernet.generate_key().decode()
SETTINGS = dict(
    MIKROTIK_ENABLED=True,
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_DEFAULT_PROFILE='hub-full',
    MIKROTIK_USER_PREFIX='hub-',
    MIKROTIK_CREDENTIAL_KEY=TEST_KEY,
)


class FakeRouterOSClient:
    def __init__(self):
        self.users = {}
        self.sessions = []
        self.creates = 0
        self.updates = 0

    def system_resource(self):
        return {'version': '7.20'}

    def find_profile(self, name):
        return {'.id': '*1', 'name': name}

    def find_hotspot_user(self, name):
        return self.users.get(name)

    def create_hotspot_user(self, values):
        self.creates += 1
        self.users[values['name']] = {'.id': '*2', **values}

    def update_hotspot_user(self, remote_id, values):
        self.updates += 1
        for user in self.users.values():
            if user['.id'] == remote_id:
                user.update(values)

    def active_sessions(self, name):
        return [row for row in self.sessions if row['user'] == name]

    def remove_active(self, remote_id):
        self.sessions = [row for row in self.sessions if row['.id'] != remote_id]


class SessionNetworkTestMixin:
    def make_session(self, *, provider=InternetSession.NetworkProvider.MANUAL,
                     bandwidth_profile=''):
        return InternetSession.objects.create(
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=provider,
            bandwidth_profile=bandwidth_profile,
        )


class ManualSessionNetworkTests(SessionNetworkTestMixin, TestCase):
    def test_manual_provision_and_disconnect_do_not_change_commercial_status(self):
        session = self.make_session()
        backend = ManualSessionNetworkBackend()

        backend.provision_access(session)
        session.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(session.network_status, PROVISIONED)
        state = InternetSessionNetworkState.objects.get(session=session)
        self.assertIsNotNone(state.last_network_sync_at)
        self.assertEqual(state.last_network_error, '')

        backend.disconnect_access(session)
        session.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(session.network_status, DISCONNECTED)

    def test_session_network_operation_is_idempotent_and_processes(self):
        session = self.make_session()
        first = enqueue_session_network_operation(
            session,
            InternetSessionNetworkOperation.Operation.PROVISION,
            process_after_commit=False,
        )
        second = enqueue_session_network_operation(
            session,
            InternetSessionNetworkOperation.Operation.PROVISION,
            process_after_commit=False,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(InternetSessionNetworkOperation.objects.count(), 1)
        self.assertTrue(process_session_network_operation(first))
        first.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(first.status, InternetSessionNetworkOperation.Status.SUCCEEDED)
        self.assertEqual(session.network_status, PROVISIONED)
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)

    def test_worker_failure_marks_network_not_commercial_state(self):
        session = self.make_session(provider=InternetSession.NetworkProvider.UNIFI)
        job = enqueue_session_network_operation(
            session,
            InternetSessionNetworkOperation.Operation.PROVISION,
            process_after_commit=False,
        )

        self.assertFalse(process_session_network_operation(job))
        job.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(job.status, InternetSessionNetworkOperation.Status.FAILED)
        self.assertEqual(session.network_status, PROVISION_ERROR)
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        state = InternetSessionNetworkState.objects.get(session=session)
        self.assertTrue(state.last_network_error)
        self.assertIsNotNone(state.last_network_sync_at)


@override_settings(**SETTINGS)
class MikroTikSessionNetworkTests(SessionNetworkTestMixin, TestCase):
    def setUp(self):
        InternetBandwidthProfile.objects.create(
            code='fast',
            name='Fast',
            router_profile_name='hub-fast',
        )
        self.client = FakeRouterOSClient()
        self.backend = MikroTikSessionNetworkBackend(client=self.client)

    def test_plan_maps_profile_and_has_no_uptime_or_shared_users(self):
        session = self.make_session(
            provider=InternetSession.NetworkProvider.MIKROTIK,
            bandwidth_profile='fast',
        )

        plan = self.backend.plan(session)

        self.assertEqual(plan['profile'], 'hub-fast')
        self.assertEqual(plan['server'], 'hub-hotspot')
        self.assertNotIn('limit-uptime', plan)
        self.assertNotIn('shared-users', plan)

    def test_provision_is_owned_encrypted_and_idempotent(self):
        session = self.make_session(
            provider=InternetSession.NetworkProvider.MIKROTIK,
            bandwidth_profile='fast',
        )

        self.backend.provision_access(session)
        session.refresh_from_db()
        username = self.backend.username(session)
        user = self.client.users[username]
        state = InternetSessionNetworkState.objects.get(session=session)

        self.assertTrue(username.startswith('hub-s-'))
        self.assertEqual(session.network_session_id, username)
        self.assertEqual(session.network_status, PROVISIONED)
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(user['comment'], f'hub-session:{session.pk}')
        self.assertNotIn('limit-uptime', user)
        self.assertNotIn('shared-users', user)
        self.assertTrue(state.network_credential_encrypted)
        plaintext = Fernet(TEST_KEY.encode()).decrypt(
            state.network_credential_encrypted.encode()
        ).decode()
        self.assertTrue(plaintext)
        self.assertNotEqual(plaintext, state.network_credential_encrypted)
        self.assertNotIn(plaintext, str(user.get('comment', '')))

        self.backend.provision_access(session)
        self.assertEqual((self.client.creates, self.client.updates), (1, 1))

    def test_foreign_username_collision_is_rejected_without_ending_session(self):
        session = self.make_session(provider=InternetSession.NetworkProvider.MIKROTIK)
        username = self.backend.username(session)
        self.client.users[username] = {
            '.id': '*9',
            'name': username,
            'comment': 'provider-owned',
        }

        with self.assertRaises(MikroTikProvisioningError):
            self.backend.provision_access(session)

        session.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(session.network_status, PROVISION_ERROR)
        state = InternetSessionNetworkState.objects.get(session=session)
        self.assertTrue(state.last_network_error)

    def test_disconnect_removes_only_matching_active_sessions(self):
        session = self.make_session(provider=InternetSession.NetworkProvider.MIKROTIK)
        self.backend.provision_access(session)
        username = self.backend.username(session)
        self.client.sessions = [
            {'.id': '*a', 'user': username},
            {'.id': '*b', 'user': 'other'},
        ]

        self.backend.disconnect_access(session)
        self.backend.disconnect_access(session)

        session.refresh_from_db()
        self.assertEqual(session.network_status, DISCONNECTED)
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(self.client.sessions, [{'.id': '*b', 'user': 'other'}])
        self.assertEqual(self.client.users[username]['disabled'], 'true')
