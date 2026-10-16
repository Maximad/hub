"""Opt-in routing must agree between staff inbox and push, with safe fallback."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from catalog.models import PrepStation
from core.models import (
    NotificationEvent, NotificationLog, NotificationPreference,
    Order, PushSubscription,
)
from core.notifications import create_notification, group_notification_recipients_for_user
from core.services.notification_delivery import (
    enqueue_push_deliveries_for_event, user_is_push_target,
)
from operations.duty import (
    accept_duty_handover, assign_duty_coverage, request_duty_handover,
)
from operations.opening import set_staff_roster
from operations.services import open_business_day


PUSH_ENABLED = override_settings(
    PUSH_NOTIFICATIONS_ENABLED=True,
    DUTY_NOTIFICATION_ROUTING_ENABLED=True,
    PUSH_PROVIDER='webpush',
    VAPID_PUBLIC_KEY='public-key',
    VAPID_PRIVATE_KEY='private-key',
    VAPID_SUBJECT='mailto:ops@example.org',
)


@PUSH_ENABLED
class DutyNotificationRoutingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username='routing-owner', phone='+963955999301',
            email='routing@example.org', password='pass',
        )
        self.tamer = User.objects.create_user(
            username='routing-tamer', phone='+963955999302', role=User.Role.CASHIER,
        )
        self.salwa = User.objects.create_user(
            username='routing-salwa', phone='+963955999303', role=User.Role.CASHIER,
        )
        self.waiter = User.objects.create_user(
            username='routing-waiter', phone='+963955999304', role=User.Role.WAITER,
        )
        self.kitchen = User.objects.create_user(
            username='routing-kitchen', phone='+963955999305', role=User.Role.KITCHEN,
        )
        self.bar = User.objects.create_user(
            username='routing-bar', phone='+963955999306', role=User.Role.BARTENDER,
        )
        self.users = (self.owner, self.tamer, self.salwa, self.waiter, self.kitchen, self.bar)
        for user in self.users:
            NotificationPreference.objects.create(user=user, enable_browser_notifications=True)
            PushSubscription.objects.create(
                user=user, endpoint=f'https://fcm.googleapis.com/fcm/send/duty-{user.pk}',
                p256dh=f'duty-{user.pk}', auth_secret=f'duty-secret-{user.pk}',
            )
        self.day = open_business_day(actor=self.owner)
        set_staff_roster(
            self.day, actor=self.owner, user_ids=[u.pk for u in self.users],
        )
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        self.order = Order.objects.create()
        self.kitchen_station, _ = PrepStation.objects.get_or_create(
            code='kitchen', defaults={'name_ar': 'المطبخ', 'station_type': 'kitchen'},
        )
        self.bar_station, _ = PrepStation.objects.get_or_create(
            code='bar', defaults={'name_ar': 'مشاريب', 'station_type': 'bar'},
        )

    def _event(self, event_type='new_order', station=None):
        with self.captureOnCommitCallbacks(execute=True):
            event = create_notification(
                event_type, 'اختبار المناوبة', order=self.order,
                target_station=station,
            )
        self.assertIsNotNone(event)
        return event

    def _visible(self, event, user):
        keys = {f'event:{event.pk}'}
        if event.event_type == 'new_prep_item':
            keys.add(
                f'prep-order:{self.order.pk}:station:{event.target_station_id or "none"}'
            )
        return any(
            card['group_key'] in keys
            for card in group_notification_recipients_for_user(user)
        )

    def _pushed_to(self, event):
        return set(
            NotificationLog.objects.filter(
                notification_event=event, channel=NotificationLog.Channel.BROWSER,
            ).values_list('recipient_user_id', flat=True)
        )

    def test_active_service_holder_only_gets_new_order_in_app_and_push(self):
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.salwa.pk, actor=self.owner,
        )
        event = self._event()
        self.assertEqual(self._pushed_to(event), {self.salwa.pk})
        self.assertTrue(self._visible(event, self.salwa))
        self.assertFalse(self._visible(event, self.tamer))
        self.assertFalse(self._visible(event, self.owner))
        self.assertEqual(event.duty_routing_business_date, self.day.business_date)

    def test_cashier_covers_new_order_only_when_service_is_unassigned(self):
        assign_duty_coverage(
            self.day, responsibility='cashier', user_id=self.tamer.pk, actor=self.owner,
        )
        event = self._event()
        self.assertEqual(self._pushed_to(event), {self.tamer.pk})
        self.assertTrue(self._visible(event, self.tamer))
        self.assertFalse(self._visible(event, self.salwa))

    def test_manager_fallback_when_no_operational_duty_is_assigned(self):
        event = self._event()
        self.assertEqual(self._pushed_to(event), {self.owner.pk})
        self.assertTrue(self._visible(event, self.owner))
        self.assertFalse(self._visible(event, self.tamer))

    def test_handover_changes_live_inbox_and_new_push_recipient(self):
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.tamer.pk, actor=self.owner,
        )
        event = self._event()
        request_duty_handover(
            self.day, responsibility='service', to_user_id=self.salwa.pk, actor=self.tamer,
        )
        self.assertTrue(self._visible(event, self.tamer))
        self.assertFalse(self._visible(event, self.salwa))
        accept_duty_handover(self.day, responsibility='service', actor=self.salwa)
        self.assertFalse(self._visible(event, self.tamer))
        self.assertTrue(self._visible(event, self.salwa))
        next_event = self._event()
        self.assertEqual(self._pushed_to(next_event), {self.salwa.pk})

    def test_preparation_follows_station_and_not_all_staff(self):
        assign_duty_coverage(
            self.day, responsibility='kitchen', user_id=self.kitchen.pk, actor=self.owner,
        )
        assign_duty_coverage(
            self.day, responsibility='bar', user_id=self.bar.pk, actor=self.owner,
        )
        kitchen_event = self._event('new_prep_item', self.kitchen_station)
        bar_event = self._event('new_prep_item', self.bar_station)
        self.assertEqual(self._pushed_to(kitchen_event), {self.kitchen.pk})
        self.assertEqual(self._pushed_to(bar_event), {self.bar.pk})
        self.assertTrue(self._visible(kitchen_event, self.kitchen))
        self.assertFalse(self._visible(kitchen_event, self.bar))
        self.assertTrue(self._visible(bar_event, self.bar))
        self.assertFalse(self._visible(bar_event, self.kitchen))

    def test_ready_for_pickup_has_in_app_record_for_on_duty_waiter(self):
        # Legacy ready-item recipients are admin/kitchen; new duty audience
        # must not depend on those legacy role labels.
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.waiter.pk, actor=self.owner,
        )
        event = self._event('prep_item_ready', self.kitchen_station)
        self.assertEqual(self._pushed_to(event), {self.waiter.pk})
        self.assertTrue(self._visible(event, self.waiter))
        self.assertFalse(self._visible(event, self.kitchen))
        self.assertFalse(self._visible(event, self.owner))

    def test_capability_override_revokes_duty_alert_and_escalates(self):
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.tamer.pk, actor=self.owner,
        )
        UserCapabilityOverride.objects.create(
            user=self.tamer, capability='orders', allowed=False,
        )
        event = self._event()
        self.assertEqual(self._pushed_to(event), {self.owner.pk})
        self.assertTrue(self._visible(event, self.owner))
        self.assertFalse(self._visible(event, self.tamer))

    def test_flag_disabled_preserves_existing_role_broadcast(self):
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.salwa.pk, actor=self.owner,
        )
        with override_settings(DUTY_NOTIFICATION_ROUTING_ENABLED=False):
            event = self._event()
            self.assertIsNone(event.duty_routing_business_date)
            self.assertTrue(self._visible(event, self.tamer))
            self.assertTrue(self._visible(event, self.salwa))
            self.assertEqual(self._pushed_to(event), {
                self.owner.pk, self.tamer.pk, self.salwa.pk, self.waiter.pk,
            })

    def test_explicit_old_notification_not_retroactively_rerouted(self):
        with override_settings(DUTY_NOTIFICATION_ROUTING_ENABLED=False):
            event = self._event()
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.salwa.pk, actor=self.owner,
        )
        self.assertTrue(self._visible(event, self.tamer))
        self.assertTrue(self._visible(event, self.owner))

    def test_guest_wifi_pin_and_manager_approvals_not_duty_routed(self):
        for kind in ('guest_wifi_daily_pin', 'manager_approval_needed'):
            event = self._event(kind)
            self.assertIsNone(event.duty_routing_business_date)
            if kind == 'guest_wifi_daily_pin':
                self.assertEqual(self._pushed_to(event), set())
            else:
                self.assertIn(self.owner.pk, self._pushed_to(event))

    def test_queue_claim_is_rechecked_after_handover(self):
        assign_duty_coverage(
            self.day, responsibility='service', user_id=self.tamer.pk, actor=self.owner,
        )
        event = self._event()
        request_duty_handover(
            self.day, responsibility='service', to_user_id=self.salwa.pk, actor=self.tamer,
        )
        accept_duty_handover(self.day, responsibility='service', actor=self.salwa)
        self.assertFalse(user_is_push_target(event, self.tamer))
        self.assertTrue(user_is_push_target(event, self.salwa))
