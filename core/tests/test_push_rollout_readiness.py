import json
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.models import NotificationEvent, NotificationLog, PushSubscription


VALID_PUSH_SETTINGS = {
    'PUSH_NOTIFICATIONS_ENABLED': True,
    'PUSH_PROVIDER': 'webpush',
    'VAPID_PUBLIC_KEY': 'public-key',
    'VAPID_PRIVATE_KEY': 'private-key',
    'VAPID_SUBJECT': 'mailto:ops@example.com',
    'PUSH_ENDPOINT_ALLOWED_HOSTS': ('fcm.googleapis.com',),
}


class PushForegroundContractTests(SimpleTestCase):
    def test_service_worker_suppresses_os_alert_for_visible_staff_client(self):
        source = (Path(settings.BASE_DIR) / 'templates' / 'service-worker.js').read_text()
        self.assertIn("client.visibilityState !== 'visible'", source)
        self.assertIn("url.pathname.indexOf('/staff/') === 0", source)
        self.assertIn("client.postMessage({type: 'hub-push'", source)
        self.assertIn('self.registration.showNotification', source)

    def test_staff_polling_avoids_second_browser_alert_when_background_push_is_active(self):
        source = (Path(settings.BASE_DIR) / 'static' / 'js' / 'staff_notifications.js').read_text()
        self.assertIn('!backgroundActive && !suppressNextBrowserAlert', source)
        self.assertIn("serviceWorker.addEventListener('message'", source)
        self.assertIn("event.data.type !== 'hub-push'", source)
        self.assertIn("document.addEventListener('visibilitychange'", source)


class PushReadinessTests(TestCase):
    def run_json(self):
        output = StringIO()
        call_command('push_readiness', '--json', stdout=output)
        return json.loads(output.getvalue())

    @override_settings(PUSH_NOTIFICATIONS_ENABLED=False)
    def test_disabled_push_is_clean_and_needs_no_credentials(self):
        payload = self.run_json()
        self.assertEqual(payload['status'], 'PASS')
        self.assertFalse(payload['enabled'])
        self.assertEqual(payload['checks'][0]['code'], 'push_disabled')

    @override_settings(**VALID_PUSH_SETTINGS)
    def test_enabled_push_warns_until_a_test_device_is_registered(self):
        payload = self.run_json()
        rows = {row['code']: row for row in payload['checks']}
        self.assertEqual(rows['push_configuration']['status'], 'PASS')
        self.assertEqual(rows['push_subscriptions']['status'], 'WARN')
        self.assertEqual(rows['push_queue']['status'], 'PASS')
        self.assertEqual(rows['push_recent_failures']['status'], 'PASS')

    @override_settings(**VALID_PUSH_SETTINGS)
    def test_stale_queue_and_recent_failures_are_visible_without_credentials(self):
        User = get_user_model()
        user = User.objects.create_user(
            username='push-readiness-admin',
            password='pass',
            phone='+963000009001',
            role='admin',
        )
        subscription = PushSubscription.objects.create(
            user=user,
            endpoint='https://fcm.googleapis.com/fcm/send/readiness-device',
            p256dh='READINESS-P256DH-SECRET',
            auth_secret='READINESS-AUTH-SECRET',
            device_label='admin-phone',
        )
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.MANAGER_APPROVAL_NEEDED,
            title_ar='موافقة',
        )
        stale_time = timezone.now() - timedelta(minutes=20)
        NotificationLog.objects.create(
            notification_event=event,
            channel='browser',
            push_subscription=subscription,
            recipient_user=user,
            status='pending',
            next_attempt_at=stale_time,
            dedupe_key='readiness-pending',
        )
        failed = NotificationLog.objects.create(
            notification_event=event,
            channel='browser',
            push_subscription=subscription,
            recipient_user=user,
            status='failed',
            error_code='provider_http_503',
            dedupe_key='readiness-failed',
        )
        failed.updated_at = timezone.now()
        failed.save(update_fields=('updated_at',))

        payload = self.run_json()
        rows = {row['code']: row for row in payload['checks']}
        self.assertEqual(rows['push_subscriptions']['status'], 'PASS')
        self.assertEqual(rows['push_queue']['status'], 'WARN')
        self.assertEqual(rows['push_recent_failures']['status'], 'WARN')

        rendered = json.dumps(payload)
        self.assertNotIn('READINESS-P256DH-SECRET', rendered)
        self.assertNotIn('READINESS-AUTH-SECRET', rendered)
        self.assertNotIn('readiness-device', rendered)

    @override_settings(
        PUSH_NOTIFICATIONS_ENABLED=True,
        PUSH_PROVIDER='webpush',
        VAPID_PUBLIC_KEY='',
        VAPID_PRIVATE_KEY='private-key',
        VAPID_SUBJECT='mailto:ops@example.com',
        PUSH_ENDPOINT_ALLOWED_HOSTS=('fcm.googleapis.com',),
    )
    def test_invalid_enabled_configuration_fails(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_json()
        self.assertEqual(raised.exception.code, 1)
