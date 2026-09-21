from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import ActivityLog, InternetBandwidthProfile
from core.services.mikrotik import RouterOSClient
from core.services.mikrotik_portal_policy import (
    configured_router_mappings,
    inspect_router_mappings,
)
from internet.models import GuestWifiPolicy


class ReadOnlyPolicyRouter:
    def __init__(self):
        self.server = {
            '.id': '*1',
            'name': 'hub-hotspot',
            'disabled': 'false',
            'invalid': 'false',
        }
        self.profiles = {
            'hub-slow': {
                '.id': '*2',
                'name': 'hub-slow',
                'rate-limit': '999M/777M',
                'shared-users': '42',
                'disabled': 'false',
                'comment': 'provider-owned',
            },
            'hub-full': {
                '.id': '*3',
                'name': 'hub-full',
                'rate-limit': '1G/1G',
                'shared-users': '7',
                'disabled': 'false',
                'comment': 'provider-owned',
            },
        }

    def find_hotspot_server(self, name):
        return self.server if name == self.server['name'] else None

    def find_profile(self, name):
        return self.profiles.get(name)

    def create_profile(self, values):
        raise AssertionError('configuration writes are forbidden')

    def update_profile(self, remote_id, values):
        raise AssertionError('configuration writes are forbidden')

    def create_walled_garden(self, values):
        raise AssertionError('configuration writes are forbidden')

    def update_walled_garden(self, remote_id, values):
        raise AssertionError('configuration writes are forbidden')


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_BASE_URL='https://router.example.test/rest',
    MIKROTIK_USERNAME='hub-service',
    MIKROTIK_PASSWORD='never-render-me',
    MIKROTIK_VERIFY_TLS=True,
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_CUSTOMER_PROFILE_CODE='fast',
)
class MikroTikRouterBoundaryTests(TestCase):
    def setUp(self):
        self.basic = InternetBandwidthProfile.objects.create(
            code='basic',
            name='Basic',
            upload_limit_kbps=1000,
            download_limit_kbps=2000,
            router_profile_name='hub-slow',
        )
        self.fast = InternetBandwidthProfile.objects.create(
            code='fast',
            name='Fast',
            upload_limit_kbps=50000,
            download_limit_kbps=150000,
            router_profile_name='hub-full',
        )
        policy = GuestWifiPolicy.objects.get(key='default')
        policy.enabled = True
        policy.bandwidth_profile = self.basic
        policy.save(update_fields=['enabled', 'bandwidth_profile', 'updated_at'])
        self.router = ReadOnlyPolicyRouter()

    def _login_admin(self):
        user = get_user_model().objects.create_superuser(
            username='portal-admin',
            password='pass',
            email='portal@example.com',
            phone='+963900009999',
        )
        self.client.force_login(user)
        return user

    def test_configured_mappings_keep_existing_router_names(self):
        mappings = configured_router_mappings()

        self.assertEqual(mappings['server'], 'hub-hotspot')
        self.assertEqual(
            [item['name'] for item in mappings['profiles']],
            ['hub-slow', 'hub-full'],
        )

    def test_readonly_mapping_check_ignores_provider_owned_properties(self):
        report = inspect_router_mappings(client=self.router)

        self.assertTrue(report['ready'])
        self.assertEqual(
            [item['code'] for item in report['checks']],
            ['server', 'profile:basic', 'profile:fast'],
        )

    def test_missing_mapped_profile_is_reported_without_repair(self):
        self.router.profiles.pop('hub-full')

        report = inspect_router_mappings(client=self.router)

        self.assertFalse(report['ready'])
        self.assertFalse(
            next(item for item in report['checks'] if item['code'] == 'profile:fast')['ok']
        )

    def test_router_client_has_no_profile_or_walled_garden_write_helpers(self):
        for method in (
            'create_profile',
            'update_profile',
            'create_walled_garden',
            'update_walled_garden',
        ):
            self.assertFalse(hasattr(RouterOSClient, method))

    def test_retired_sync_post_is_rejected_without_router_work(self):
        self._login_admin()

        response = self.client.post(reverse('staff_internet_settings'), {
            'operation_action': 'sync_mikrotik_portal_policy',
        })

        self.assertEqual(response.status_code, 302)
        log = ActivityLog.objects.get(
            action='internet.mikrotik_portal_policy_sync_rejected'
        )
        self.assertEqual(
            log.details['reason'],
            'router_configuration_is_provider_managed',
        )

    def test_profile_edit_cannot_change_historical_speed_values(self):
        self._login_admin()

        response = self.client.post(
            reverse('staff_internet_profile_edit', args=[self.basic.pk]),
            {
                'code': 'basic',
                'name': 'Basic',
                'router_profile_name': 'hub-slow',
                'is_active': 'on',
                'download_limit_kbps': '123456',
                'upload_limit_kbps': '654321',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.basic.refresh_from_db()
        self.assertEqual(self.basic.download_limit_kbps, 2000)
        self.assertEqual(self.basic.upload_limit_kbps, 1000)

    def test_settings_page_exposes_no_sync_or_speed_edit_controls(self):
        self._login_admin()

        response = self.client.get(reverse('staff_internet_settings'))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('sync_mikrotik_portal_policy', content)
        self.assertNotIn('name="download_limit_kbps"', content)
        self.assertNotIn('name="upload_limit_kbps"', content)
        self.assertIn('hub-slow', content)
        self.assertIn('hub-full', content)
