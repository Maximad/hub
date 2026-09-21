from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import ActivityLog, InternetBandwidthProfile
from core.services.mikrotik_portal_policy import (
    inspect_portal_policy,
    sync_portal_policy,
)
from internet.models import GuestWifiPolicy


class FakePolicyRouter:
    def __init__(self):
        self.server = {
            '.id': '*1', 'name': 'hub-hotspot', 'disabled': 'false', 'invalid': 'false',
        }
        self.profiles = {
            'unrelated': {
                '.id': '*9', 'name': 'unrelated', 'rate-limit': '9M/9M',
                'shared-users': '9', 'disabled': 'false', 'comment': 'provider-owned',
            },
        }
        self.garden = {}
        self.updated_profiles = []

    def find_hotspot_server(self, name):
        return self.server if name == self.server['name'] else None

    def find_profile(self, name):
        return self.profiles.get(name)

    def create_profile(self, values):
        self.profiles[values['name']] = {'.id': f"*p{len(self.profiles)}", 'disabled': 'false', **values}

    def update_profile(self, remote_id, values):
        self.updated_profiles.append(remote_id)
        for profile in self.profiles.values():
            if profile['.id'] == remote_id:
                profile.update(values)

    def find_walled_garden(self, *, server, dst_host):
        return self.garden.get((server, dst_host))

    def create_walled_garden(self, values):
        self.garden[(values['server'], values['dst-host'])] = {
            '.id': '*w1', 'disabled': 'false', **values,
        }

    def update_walled_garden(self, remote_id, values):
        for rule in self.garden.values():
            if rule['.id'] == remote_id:
                rule.update(values)


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_BASE_URL='https://router.example.test/rest',
    MIKROTIK_USERNAME='hub-service',
    MIKROTIK_PASSWORD='never-render-me',
    MIKROTIK_VERIFY_TLS=True,
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_PORTAL_HOST='hubsweida.jwtalenthouse.com',
    MIKROTIK_CUSTOMER_PROFILE_CODE='fast',
)
class MikroTikPortalPolicyTests(TestCase):
    def setUp(self):
        self.basic = InternetBandwidthProfile.objects.create(
            code='basic', name='Basic', upload_limit_kbps=1000,
            download_limit_kbps=2000, router_profile_name='hub-slow',
        )
        self.fast = InternetBandwidthProfile.objects.create(
            code='fast', name='Fast', upload_limit_kbps=50000,
            download_limit_kbps=150000, router_profile_name='hub-full',
        )
        policy = GuestWifiPolicy.objects.get(key='default')
        policy.enabled = True
        policy.bandwidth_profile = self.basic
        policy.save(update_fields=['enabled', 'bandwidth_profile', 'updated_at'])
        self.router = FakePolicyRouter()

    def test_sync_creates_only_two_profiles_and_portal_access(self):
        report = sync_portal_policy(client=self.router)

        self.assertTrue(report['ready'])
        self.assertEqual(self.router.profiles['hub-slow']['rate-limit'], '1000k/2000k')
        self.assertEqual(self.router.profiles['hub-full']['rate-limit'], '50000k/150000k')
        self.assertEqual(self.router.profiles['hub-slow']['shared-users'], '1')
        self.assertEqual(self.router.profiles['unrelated']['rate-limit'], '9M/9M')
        rule = self.router.garden[
            ('hub-hotspot', 'hubsweida.jwtalenthouse.com')
        ]
        self.assertEqual(rule['action'], 'allow')
        self.assertEqual(len(report['changes']), 3)

    def test_sync_is_idempotent_and_accepts_equivalent_rate_units(self):
        sync_portal_policy(client=self.router)
        self.router.profiles['hub-slow']['rate-limit'] = '1M/2M'
        second = sync_portal_policy(client=self.router)

        self.assertTrue(second['ready'])
        self.assertEqual(second['changes'], [])
        self.assertTrue(inspect_portal_policy(client=self.router)['ready'])

    @patch('core.views.internet_settings.sync_portal_policy')
    def test_staff_button_audits_secret_free_sync(self, sync):
        sync.return_value = {'ready': True, 'changes': [], 'checks': []}
        user = get_user_model().objects.create_superuser(
            username='portal-admin', password='pass', email='portal@example.com',
            phone='+963900009999',
        )
        self.client.force_login(user)

        response = self.client.post(reverse('staff_internet_settings'), {
            'operation_action': 'sync_mikrotik_portal_policy',
        })

        self.assertEqual(response.status_code, 302)
        sync.assert_called_once_with()
        log = ActivityLog.objects.get(action='internet.mikrotik_portal_policy_synced')
        self.assertTrue(log.details['ready'])
        self.assertNotIn('never-render-me', str(log.details))
