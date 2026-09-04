from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import NotificationLog, NotificationPreference, Order, PushSubscription
from core.notifications import notify_order_created


@override_settings(
    PUSH_NOTIFICATIONS_ENABLED=True,
    PUSH_PROVIDER='webpush',
    VAPID_PUBLIC_KEY='public-key',
    VAPID_PRIVATE_KEY='private-key',
    VAPID_SUBJECT='mailto:ops@example.com',
)
class OrderNotificationEnqueueRegressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='order-push-admin',
            password='pass',
            phone='+963000009001',
            role='admin',
        )
        NotificationPreference.objects.create(
            user=self.admin,
            enable_browser_notifications=True,
            notify_new_orders=True,
        )
        PushSubscription.objects.create(
            user=self.admin,
            endpoint='https://fcm.googleapis.com/fcm/send/order-push-admin',
            p256dh='p256dh-order-push-admin',
            auth_secret='auth-order-push-admin',
            device_label='admin-device',
        )
        self.order = Order.objects.create()

    def test_order_push_is_queued_even_if_nested_on_commit_callback_is_deferred(self):
        # Production orders showed the system NEW_ORDER event while its browser
        # delivery row was absent. Prep rows created moments later were present.
        # Simulate that nested callback timing gap: the explicit idempotent order
        # enqueue must still create the browser delivery row.
        with patch('core.notifications.transaction.on_commit'):
            notify_order_created(self.order)

        logs = NotificationLog.objects.filter(
            channel=NotificationLog.Channel.BROWSER,
            recipient_user=self.admin,
            notification_event__order=self.order,
            notification_event__event_type='new_order',
        )
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().dedupe_key, f'event:{logs.get().notification_event_id}')

    def test_regular_commit_callback_and_explicit_enqueue_remain_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify_order_created(self.order)

        logs = NotificationLog.objects.filter(
            channel=NotificationLog.Channel.BROWSER,
            recipient_user=self.admin,
            notification_event__order=self.order,
            notification_event__event_type='new_order',
        )
        self.assertEqual(logs.count(), 1)
