from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from catalog.models import PrepStation
from core.models import (
    NotificationEvent,
    NotificationLog,
    NotificationPreference,
    NotificationRecipient,
    Order,
    PushSubscription,
)
from core.notifications import create_notification
from core.services.notification_delivery import (
    _claim_next_delivery,
    build_push_payload,
    enqueue_push_deliveries_for_event,
    push_dedupe_key,
    run_notification_worker_cycle,
)
from core.services.push import PushSendResult, PushTransportError


PUSH_ENABLED = override_settings(
    PUSH_NOTIFICATIONS_ENABLED=True,
    PUSH_PROVIDER='webpush',
    VAPID_PUBLIC_KEY='public-key',
    VAPID_PRIVATE_KEY='private-key',
    VAPID_SUBJECT='mailto:ops@example.com',
)


class FakeTransport:
    provider = 'webpush'

    def __init__(self, outcome='success'):
        self.outcome = outcome
        self.calls = []

    def send(self, subscription, payload):
        self.calls.append((subscription.pk, payload.as_dict()))
        if self.outcome == 'temporary':
            raise PushTransportError('provider_http_503', permanent=False, status_code=503)
        if self.outcome == 'gone':
            raise PushTransportError('subscription_gone', permanent=True, status_code=410)
        return PushSendResult(accepted=True, status_code=201)


@PUSH_ENABLED
class NotificationDeliveryRoutingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='push-admin', password='pass', phone='+963000001001', role='admin'
        )
        self.cashier = User.objects.create_user(
            username='push-cashier', password='pass', phone='+963000001002', role='cashier'
        )
        self.waiter = User.objects.create_user(
            username='push-waiter', password='pass', phone='+963000001003', role='waiter'
        )
        self.kitchen = User.objects.create_user(
            username='push-kitchen', password='pass', phone='+963000001004', role='kitchen'
        )
        for user in (self.admin, self.cashier, self.waiter, self.kitchen):
            NotificationPreference.objects.create(
                user=user,
                enable_browser_notifications=True,
            )
            PushSubscription.objects.create(
                user=user,
                endpoint=f'https://fcm.googleapis.com/fcm/send/{user.pk}',
                p256dh=f'p256dh-{user.pk}',
                auth_secret=f'auth-{user.pk}',
                device_label=f'device-{user.pk}',
            )
        self.kitchen_station, _ = PrepStation.objects.get_or_create(
            code='kitchen',
            defaults={
                'name_ar': 'المطبخ',
                'station_type': 'kitchen',
            },
        )
        self.order = Order.objects.create()

    def queued_users(self):
        return set(
            NotificationLog.objects.filter(
                channel=NotificationLog.Channel.BROWSER,
                status=NotificationLog.Status.PENDING,
            ).values_list('recipient_user__username', flat=True)
        )

    def test_new_order_routes_to_admin_cashier_and_service_not_kitchen(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
            order=self.order,
        )
        created = enqueue_push_deliveries_for_event(event)
        self.assertEqual(created, 3)
        self.assertEqual(
            self.queued_users(),
            {'push-admin', 'push-cashier', 'push-waiter'},
        )

    def test_prep_order_is_grouped_by_order_and_station_for_kitchen_only(self):
        first = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
            title_ar='عنصر جديد',
            message_ar='SECRET ITEM ONE',
            order=self.order,
            target_station=self.kitchen_station,
        )
        second = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
            title_ar='عنصر آخر',
            message_ar='SECRET ITEM TWO',
            order=self.order,
            target_station=self.kitchen_station,
        )
        enqueue_push_deliveries_for_event(first)
        enqueue_push_deliveries_for_event(second)

        logs = NotificationLog.objects.filter(channel=NotificationLog.Channel.BROWSER)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(set(logs.values_list('recipient_user__username', flat=True)), {'push-kitchen'})
        self.assertEqual(set(logs.values_list('dedupe_key', flat=True)), {push_dedupe_key(first)})

        payload = build_push_payload(first).as_dict()
        self.assertIn('2 عناصر', payload['body'])
        self.assertNotIn('SECRET ITEM ONE', str(payload))
        self.assertNotIn('SECRET ITEM TWO', str(payload))

    def test_ready_item_routes_to_service_not_station_operator_or_admin(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.PREP_ITEM_READY,
            title_ar='عنصر جاهز',
            order=self.order,
            target_station=self.kitchen_station,
        )
        enqueue_push_deliveries_for_event(event)
        self.assertEqual(self.queued_users(), {'push-waiter'})

    def test_manager_approval_is_admin_only(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.MANAGER_APPROVAL_NEEDED,
            title_ar='موافقة',
            order=self.order,
        )
        enqueue_push_deliveries_for_event(event)
        self.assertEqual(self.queued_users(), {'push-admin'})

    def test_capability_deny_override_blocks_push_even_when_role_matches(self):
        UserCapabilityOverride.objects.create(
            user=self.cashier,
            capability='orders',
            allowed=False,
        )
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
            order=self.order,
        )
        enqueue_push_deliveries_for_event(event)
        self.assertEqual(self.queued_users(), {'push-admin', 'push-waiter'})

    def test_kitchen_capability_deny_override_blocks_prep_push(self):
        UserCapabilityOverride.objects.create(
            user=self.kitchen,
            capability='kitchen_board',
            allowed=False,
        )
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
            title_ar='عنصر جديد',
            order=self.order,
            target_station=self.kitchen_station,
        )
        self.assertEqual(enqueue_push_deliveries_for_event(event), 0)
        self.assertFalse(self.queued_users())

    def test_payment_and_daily_close_do_not_generate_push(self):
        for event_type in (
            NotificationEvent.EventType.PAYMENT_PENDING,
            NotificationEvent.EventType.CLOSE_DAY_FINALIZED,
        ):
            event = NotificationEvent.objects.create(event_type=event_type, title_ar='داخلي')
            self.assertEqual(enqueue_push_deliveries_for_event(event), 0)
        self.assertFalse(NotificationLog.objects.filter(channel=NotificationLog.Channel.BROWSER).exists())

    def test_user_category_and_browser_preferences_are_applied(self):
        pref = self.cashier.notification_preference
        pref.notify_new_orders = False
        pref.save(update_fields=('notify_new_orders', 'updated_at'))
        other_pref = self.waiter.notification_preference
        other_pref.enable_browser_notifications = False
        other_pref.save(update_fields=('enable_browser_notifications', 'updated_at'))

        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
            order=self.order,
        )
        enqueue_push_deliveries_for_event(event)
        self.assertEqual(self.queued_users(), {'push-admin'})

    def test_generic_payload_never_reuses_private_event_message_or_delivery_details(self):
        self.order.fulfillment_mode = Order.FulfillmentMode.DELIVERY
        self.order.delivery_phone = '+963-SECRET-PHONE'
        self.order.delivery_address = 'SECRET ADDRESS'
        self.order.delivery_notes = 'SECRET NOTE'
        self.order.save(update_fields=(
            'fulfillment_mode', 'delivery_phone', 'delivery_address', 'delivery_notes', 'updated_at'
        ))
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.DELIVERY_ORDER_CREATED,
            title_ar='private title should not be reused',
            message_ar='SECRET ADDRESS +963-SECRET-PHONE SECRET NOTE',
            order=self.order,
        )
        payload = str(build_push_payload(event).as_dict())
        self.assertIn('طلب توصيل جديد', payload)
        self.assertNotIn('SECRET', payload)
        self.assertNotIn('+963', payload)

    def test_create_notification_enqueues_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            event = create_notification(
                NotificationEvent.EventType.NEW_ORDER,
                'طلب جديد',
                order=self.order,
            )
        self.assertIsNotNone(event)
        self.assertEqual(self.queued_users(), {'push-admin', 'push-cashier', 'push-waiter'})

    def test_explicit_and_role_admin_recipients_do_not_duplicate_device_alerts(self):
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=('is_superuser', 'is_staff'))
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
            order=self.order,
        )
        NotificationRecipient.objects.create(notification_event=event, role='admin')
        NotificationRecipient.objects.create(notification_event=event, user=self.admin, role='admin')
        enqueue_push_deliveries_for_event(event)
        self.assertEqual(
            NotificationLog.objects.filter(
                channel=NotificationLog.Channel.BROWSER,
                recipient_user=self.admin,
            ).count(),
            1,
        )


@PUSH_ENABLED
class NotificationDeliveryWorkerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='worker-admin', password='pass', phone='+963000002001', role='admin'
        )
        self.preference = NotificationPreference.objects.create(
            user=self.user,
            enable_browser_notifications=True,
        )
        self.subscription = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://fcm.googleapis.com/fcm/send/worker',
            p256dh='p256dh-worker',
            auth_secret='auth-worker',
        )
        self.event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.MANAGER_APPROVAL_NEEDED,
            title_ar='موافقة',
        )
        self.log = NotificationLog.objects.create(
            notification_event=self.event,
            channel=NotificationLog.Channel.BROWSER,
            push_subscription=self.subscription,
            recipient_user=self.user,
            status=NotificationLog.Status.PENDING,
            next_attempt_at=timezone.now(),
            dedupe_key=f'event:{self.event.pk}',
        )

    def test_success_marks_provider_acceptance_without_marking_human_acknowledgement(self):
        recipient = NotificationRecipient.objects.create(
            notification_event=self.event,
            user=self.user,
            role='admin',
        )
        transport = FakeTransport()
        summary, errors = run_notification_worker_cycle(limit=10, transport=transport)
        self.assertFalse(errors)
        self.assertEqual(summary['sent'], 1)
        self.log.refresh_from_db()
        recipient.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.SENT)
        self.assertIsNotNone(self.log.sent_at)
        self.assertEqual(self.log.attempt_count, 1)
        self.assertIsNone(recipient.read_at)
        self.assertIsNone(recipient.delivered_at)
        self.assertEqual(len(transport.calls), 1)

    def test_temporary_failure_uses_backoff_and_keeps_subscription_active(self):
        before = timezone.now()
        summary, _ = run_notification_worker_cycle(limit=1, transport=FakeTransport('temporary'))
        self.assertEqual(summary['retried'], 1)
        self.log.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.PENDING)
        self.assertEqual(self.log.error_code, 'provider_http_503')
        self.assertGreater(self.log.next_attempt_at, before)
        self.assertTrue(self.subscription.is_active)
        self.assertEqual(self.subscription.failure_count, 1)
        self.assertNotIn('auth-worker', self.log.error_message)
        self.assertNotIn('fcm.googleapis.com', self.log.error_message)

    def test_permanent_gone_response_deactivates_subscription(self):
        summary, _ = run_notification_worker_cycle(limit=1, transport=FakeTransport('gone'))
        self.assertEqual(summary['failed'], 1)
        self.log.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.FAILED)
        self.assertEqual(self.log.error_code, 'subscription_gone')
        self.assertFalse(self.subscription.is_active)
        self.assertIsNotNone(self.subscription.revoked_at)

    def test_worker_rechecks_preference_before_send(self):
        self.preference.enable_browser_notifications = False
        self.preference.save(update_fields=('enable_browser_notifications', 'updated_at'))
        transport = FakeTransport()
        summary, _ = run_notification_worker_cycle(limit=1, transport=transport)
        self.assertEqual(summary['skipped'], 1)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.SKIPPED)
        self.assertEqual(self.log.error_code, 'preference_disabled')
        self.assertFalse(transport.calls)

    def test_worker_rechecks_effective_capability_before_send(self):
        UserCapabilityOverride.objects.create(
            user=self.user,
            capability='partial_payment_approval',
            allowed=False,
        )
        transport = FakeTransport()
        summary, _ = run_notification_worker_cycle(limit=1, transport=transport)
        self.assertEqual(summary['skipped'], 1)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.SKIPPED)
        self.assertEqual(self.log.error_code, 'recipient_not_targeted')
        self.assertFalse(transport.calls)

    def test_claim_lease_prevents_immediate_second_claim(self):
        first = _claim_next_delivery(now=timezone.now())
        second = _claim_next_delivery(now=timezone.now())
        self.assertEqual(first, self.log.pk)
        self.assertIsNone(second)

    def test_expired_event_is_skipped(self):
        self.event.expires_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=('expires_at', 'updated_at'))
        transport = FakeTransport()
        summary, _ = run_notification_worker_cycle(limit=1, transport=transport)
        self.assertEqual(summary['skipped'], 1)
        self.assertFalse(transport.calls)


class NotificationWorkerDisabledTests(TestCase):
    @override_settings(PUSH_NOTIFICATIONS_ENABLED=False)
    def test_disabled_worker_is_inert_and_command_once_succeeds(self):
        summary, errors = run_notification_worker_cycle(limit=10)
        self.assertFalse(summary['enabled'])
        self.assertEqual(summary['claimed'], 0)
        self.assertFalse(errors)
        call_command('run_notification_worker', '--once')

    @override_settings(
        PUSH_NOTIFICATIONS_ENABLED=True,
        PUSH_PROVIDER='webpush',
        VAPID_PUBLIC_KEY='public-key',
        VAPID_PRIVATE_KEY='private-key',
        VAPID_SUBJECT='mailto:ops@example.com',
    )
    @patch('core.services.notification_delivery.get_push_transport')
    def test_transport_configuration_error_is_sanitized(self, get_transport):
        get_transport.side_effect = RuntimeError('VAPID_PRIVATE_KEY=SECRET')
        summary, errors = run_notification_worker_cycle(limit=1)
        self.assertTrue(summary['enabled'])
        self.assertEqual(errors, [('transport', 'configuration_error')])
        self.assertNotIn('SECRET', str(errors))
