from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import InternetSession
from core.services.internet_readiness import mikrotik_enablement_preflight
from internet.models import (
    InternetOperationsState,
    InternetSessionNetworkOperation,
)


SAFE_MIKROTIK_SETTINGS = dict(
    MIKROTIK_ENABLED=False,
    MIKROTIK_BASE_URL='https://10.77.0.2/rest',
    MIKROTIK_USERNAME='hub-api',
    MIKROTIK_PASSWORD='do-not-render-this-password',
    MIKROTIK_VERIFY_TLS=True,
    MIKROTIK_CA_FILE='',
    MIKROTIK_CONNECT_TIMEOUT=5,
    MIKROTIK_READ_TIMEOUT=10,
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_HOTSPOT_LOGIN_URL='https://wifi.example.test/login',
    MIKROTIK_DEFAULT_PROFILE='hub-full',
    MIKROTIK_CREDENTIAL_KEY='do-not-render-this-key',
)


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    **SAFE_MIKROTIK_SETTINGS,
)
class InternetOperationsConsoleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='internet-ops-admin',
            password='pass',
            email='internet-ops@example.com',
            phone='+963900001234',
        )
        self.client.force_login(self.user)
        self.url = reverse('staff_internet_settings')

    def make_session(self):
        return InternetSession.objects.create(
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MANUAL,
        )

    def test_console_is_single_settings_route_and_does_not_render_secrets(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تشغيل وإعدادات الإنترنت')
        self.assertContains(response, 'جاهزية MikroTik')
        self.assertContains(response, 'طوابير عمليات الشبكة')
        self.assertContains(response, 'شركاء الإنترنت')
        self.assertNotContains(response, 'do-not-render-this-password')
        self.assertNotContains(response, 'do-not-render-this-key')

    def test_failed_session_operation_can_be_requeued_without_network_io(self):
        session = self.make_session()
        job = InternetSessionNetworkOperation.objects.create(
            session=session,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
            status=InternetSessionNetworkOperation.Status.FAILED,
            idempotency_key='console-session-retry',
            attempt_count=2,
            last_error='temporary safe failure',
            next_attempt_at=timezone.now() + timedelta(minutes=15),
        )

        with patch('core.services.internet_operations.RouterOSClient') as router:
            response = self.client.post(self.url, {
                'operation_action': 'retry_network_operation',
                'operation_kind': 'session',
                'operation_id': str(job.pk),
            })

        self.assertEqual(response.status_code, 302)
        router.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, InternetSessionNetworkOperation.Status.PENDING)
        self.assertIsNone(job.next_attempt_at)
        self.assertEqual(job.attempt_count, 2)
        self.assertEqual(job.last_error, 'temporary safe failure')

    def test_readonly_healthcheck_is_allowed_before_mikrotik_enablement(self):
        state = InternetOperationsState.objects.create(
            key='default',
            last_worker_seen_at=timezone.now(),
        )
        with patch(
            'core.services.internet_operations.RouterOSClient.system_resource',
            return_value={'version': '7.20'},
        ) as resource:
            response = self.client.post(self.url, {
                'operation_action': 'mikrotik_healthcheck',
            })

        self.assertEqual(response.status_code, 302)
        resource.assert_called_once_with()
        state.refresh_from_db()
        self.assertTrue(state.last_mikrotik_check_ok)
        self.assertIsNotNone(state.last_mikrotik_check_at)
        self.assertNotIn('do-not-render-this-password', state.last_mikrotik_check_message)

    def test_preflight_requires_fresh_worker_and_fresh_successful_router_check(self):
        state = InternetOperationsState.objects.create(
            key='default',
            last_worker_seen_at=timezone.now(),
            last_mikrotik_check_at=timezone.now(),
            last_mikrotik_check_ok=True,
        )

        preflight = mikrotik_enablement_preflight()
        self.assertTrue(preflight['ready'])

        state.last_worker_seen_at = timezone.now() - timedelta(minutes=2)
        state.save(update_fields=['last_worker_seen_at', 'updated_at'])
        preflight = mikrotik_enablement_preflight()
        self.assertFalse(preflight['ready'])
        worker = next(item for item in preflight['checks'] if item['code'] == 'worker')
        self.assertFalse(worker['ok'])
