from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.checks import run_checks
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import NotificationEvent, NotificationLog, PushSubscription
from core.services.push import (
    DisabledPushTransport,
    PushPayload,
    PushTransportError,
    WebPushTransport,
    get_push_transport,
)


class PushConfigurationCheckTests(SimpleTestCase):
    @override_settings(PUSH_NOTIFICATIONS_ENABLED=False)
    def test_disabled_push_requires_no_credentials(self):
        self.assertFalse([error for error in run_checks() if error.id.startswith('core.E03')])

    @override_settings(
        PUSH_NOTIFICATIONS_ENABLED=True,
        PUSH_PROVIDER='webpush',
        VAPID_PUBLIC_KEY='',
        VAPID_PRIVATE_KEY='',
        VAPID_SUBJECT='',
    )
    def test_enabled_push_requires_all_vapid_settings(self):
        self.assertIn('core.E031', {error.id for error in run_checks()})

    @override_settings(
        PUSH_NOTIFICATIONS_ENABLED=True,
        PUSH_PROVIDER='unknown',
    )
    def test_unknown_provider_is_rejected(self):
        self.assertIn('core.E030', {error.id for error in run_checks()})

    @override_settings(
        PUSH_NOTIFICATIONS_ENABLED=True,
        PUSH_PROVIDER='webpush',
        VAPID_PUBLIC_KEY='public',
        VAPID_PRIVATE_KEY='private',
        VAPID_SUBJECT='mailto:admin@example.com',
        PUSH_ENDPOINT_ALLOWED_HOSTS=(),
    )
    def test_enabled_push_requires_trusted_endpoint_hosts(self):
        self.assertIn('core.E035', {error.id for error in run_checks()})


class PushSubscriptionModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='push-user',
            password='pass',
            phone='+963000000090',
            role='admin',
        )

    def make_subscription(self, endpoint='https://push.example/subscription/one'):
        return PushSubscription.objects.create(
            user=self.user,
            endpoint=endpoint,
            p256dh='public-encryption-key',
            auth_secret='authentication-secret',
            device_label='Test phone',
        )

    def test_endpoint_is_normalized_and_stored_as_a_digest_for_lookup(self):
        subscription = self.make_subscription('  https://push.example/subscription/one  ')
        self.assertEqual(subscription.endpoint, 'https://push.example/subscription/one')
        self.assertEqual(len(subscription.endpoint_hash), 64)
        self.assertNotIn(subscription.endpoint, str(subscription))

    def test_webpush_subscription_validation_requires_https_and_keys(self):
        subscription = PushSubscription(
            user=self.user,
            endpoint='http://push.example/insecure',
            p256dh='',
            auth_secret='',
        )
        with self.assertRaises(ValidationError) as raised:
            subscription.full_clean()
        self.assertEqual(
            set(raised.exception.message_dict),
            {'endpoint', 'p256dh', 'auth_secret'},
        )

    def test_revoke_disables_subscription_without_deleting_audit_history(self):
        subscription = self.make_subscription()
        subscription.revoke()
        subscription.refresh_from_db()
        self.assertFalse(subscription.is_active)
        self.assertEqual(subscription.permission_state, PushSubscription.PermissionState.GRANTED)
        self.assertIsNotNone(subscription.revoked_at)

    def test_delivery_dedupe_is_unique_per_subscription_and_channel(self):
        subscription = self.make_subscription()
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
        )
        NotificationLog.objects.create(
            notification_event=event,
            channel=NotificationLog.Channel.BROWSER,
            push_subscription=subscription,
            recipient_user=self.user,
            dedupe_key='event:1',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationLog.objects.create(
                notification_event=event,
                channel=NotificationLog.Channel.BROWSER,
                push_subscription=subscription,
                recipient_user=self.user,
                dedupe_key='event:1',
            )


class PushTransportTests(SimpleTestCase):
    def setUp(self):
        self.subscription = SimpleNamespace(
            endpoint='https://push.example/subscription/secret-endpoint',
            p256dh='p256dh-secret',
            auth_secret='auth-secret',
        )
        self.payload = PushPayload(
            title='طلب جديد #00127',
            body='يوجد طلب جديد',
            link='/staff/orders/',
            tag='event:127',
        )

    @override_settings(PUSH_NOTIFICATIONS_ENABLED=False)
    def test_disabled_transport_never_sends(self):
        transport = get_push_transport()
        self.assertIsInstance(transport, DisabledPushTransport)
        self.assertFalse(transport.send(self.subscription, self.payload).accepted)

    def test_payload_rejects_links_outside_staff_area(self):
        with self.assertRaises(ValueError):
            PushPayload(title='Unsafe', link='https://example.com/redirect')

    @patch('core.services.push.webpush')
    def test_webpush_transport_sends_a_minimal_payload(self, send):
        send.return_value = SimpleNamespace(
            status_code=201,
            headers={},
        )
        result = WebPushTransport(
            private_key='private-vapid-key',
            subject='mailto:admin@example.com',
        ).send(self.subscription, self.payload)

        self.assertTrue(result.accepted)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.provider_message_id, '')
        call = send.call_args.kwargs
        self.assertEqual(call['subscription_info']['endpoint'], self.subscription.endpoint)
        self.assertNotIn('secret-endpoint', call['data'])
        self.assertEqual(call['timeout'], 10)

    @patch('core.services.push.webpush')
    def test_transport_marks_gone_subscriptions_permanent_without_leaking_credentials(self, send):
        provider_error = RuntimeError('auth-secret secret-endpoint')
        provider_error.response = SimpleNamespace(status_code=410)
        send.side_effect = provider_error

        with self.assertRaises(PushTransportError) as raised:
            WebPushTransport(
                private_key='private-vapid-key',
                subject='mailto:admin@example.com',
            ).send(self.subscription, self.payload)

        self.assertTrue(raised.exception.permanent)
        self.assertEqual(raised.exception.error_code, 'subscription_gone')
        self.assertNotIn('auth-secret', str(raised.exception))
