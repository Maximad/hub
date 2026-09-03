import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import NotificationPreference, PushSubscription


PUSH_SETTINGS = {
    'PUSH_NOTIFICATIONS_ENABLED': True,
    'PUSH_PROVIDER': 'webpush',
    'VAPID_PUBLIC_KEY': 'test-public-vapid-key',
    'VAPID_PRIVATE_KEY': 'test-private-vapid-key',
    'VAPID_SUBJECT': 'mailto:admin@example.com',
    'PUSH_ENDPOINT_ALLOWED_HOSTS': ('push.example', '.notify.windows.com'),
}


@override_settings(**PUSH_SETTINGS)
class PushSubscriptionApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='push-api-user',
            password='pass',
            phone='+963000000091',
            role='waiter',
        )
        self.client.force_login(self.user)
        self.url = reverse('staff_push_subscription')
        self.payload = {
            'endpoint': 'https://push.example/subscriptions/private-endpoint',
            'keys': {
                'p256dh': 'browser-public-encryption-key',
                'auth': 'browser-authentication-secret',
            },
            'device_label': 'هاتف الاختبار',
        }

    def post_subscription(self, payload=None):
        return self.client.post(
            self.url,
            data=json.dumps(payload or self.payload),
            content_type='application/json',
            HTTP_USER_AGENT='Test Browser/1.0',
        )

    def test_registration_requires_authentication(self):
        response = Client().post(
            self.url,
            data=json.dumps(self.payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_registration_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            self.url,
            data=json.dumps(self.payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    def test_registration_creates_active_subscription_without_echoing_credentials(self):
        response = self.post_subscription()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.user, self.user)
        self.assertEqual(subscription.device_label, 'هاتف الاختبار')
        self.assertEqual(subscription.user_agent, 'Test Browser/1.0')
        self.assertTrue(subscription.is_active)
        response_body = response.content.decode()
        self.assertNotIn('private-endpoint', response_body)
        self.assertNotIn('browser-authentication-secret', response_body)
        self.assertTrue(
            NotificationPreference.objects.get(user=self.user).enable_browser_notifications
        )

    def test_registration_refreshes_and_reassigns_shared_browser_endpoint(self):
        self.post_subscription()
        second_user = get_user_model().objects.create_user(
            username='push-api-second-user',
            password='pass',
            phone='+963000000092',
            role='cashier',
        )
        self.client.force_login(second_user)
        changed = dict(self.payload)
        changed['keys'] = {'p256dh': 'rotated-public-key', 'auth': 'rotated-auth-secret'}

        response = self.post_subscription(changed)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['created'])
        self.assertEqual(PushSubscription.objects.count(), 1)
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.user, second_user)
        self.assertEqual(subscription.p256dh, 'rotated-public-key')
        self.assertEqual(subscription.failure_count, 0)

    def test_registration_rejects_untrusted_endpoint_host(self):
        self.payload['endpoint'] = 'https://127.0.0.1/internal'
        response = self.post_subscription()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'invalid_subscription')
        self.assertFalse(PushSubscription.objects.exists())

    def test_registration_rejects_oversized_request(self):
        response = self.client.post(
            self.url,
            data=json.dumps({'padding': 'x' * 9000}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 413)

    def test_revocation_only_disables_the_current_users_subscription(self):
        self.post_subscription()
        response = self.client.delete(
            self.url,
            data=json.dumps(self.payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['revoked'])
        subscription = PushSubscription.objects.get()
        self.assertFalse(subscription.is_active)
        self.assertIsNotNone(subscription.revoked_at)

    @override_settings(PUSH_NOTIFICATIONS_ENABLED=False)
    def test_delivery_disabled_blocks_registration_but_allows_revocation(self):
        with self.settings(PUSH_NOTIFICATIONS_ENABLED=True):
            self.post_subscription()

        rejected = self.post_subscription()
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(rejected.json()['error'], 'push_disabled')

        revoked = self.client.delete(
            self.url,
            data=json.dumps(self.payload),
            content_type='application/json',
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()['revoked'])

    def test_config_exposes_only_public_registration_state(self):
        response = self.client.get(reverse('staff_push_config'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertTrue(response.json()['enabled'])
        self.assertEqual(response.json()['public_key'], 'test-public-vapid-key')
        self.assertNotContains(response, 'test-private-vapid-key')


class StaffPwaAssetTests(TestCase):
    def test_manifest_is_public_and_scoped_to_staff(self):
        response = self.client.get(reverse('staff_web_app_manifest'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/manifest+json'))
        manifest = json.loads(response.content)
        self.assertEqual(manifest['start_url'], '/staff/')
        self.assertEqual(manifest['scope'], '/staff/')
        self.assertEqual({icon['sizes'] for icon in manifest['icons']}, {'192x192', '512x512'})

    def test_service_worker_has_root_scope_without_authenticated_fetch_caching(self):
        response = self.client.get(reverse('service_worker'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-store', response['Cache-Control'])
        content = response.content.decode()
        self.assertIn("addEventListener('push'", content)
        self.assertIn("addEventListener('notificationclick'", content)
        self.assertNotIn("addEventListener('fetch'", content)
        self.assertIn("value.indexOf('/staff/') === 0", content)
