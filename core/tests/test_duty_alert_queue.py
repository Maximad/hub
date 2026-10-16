"""Unassigned-alert projection and post-handover reconciliation regressions."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.models import PrepStation
from accounts.models import UserCapabilityOverride
from core.duty_alert_queue import (
    event_has_outstanding_work,
    reenqueue_outstanding_after_duty_change,
    unassigned_duty_alerts_for_manager,
)
from core.models import (
    Category, NotificationEvent, NotificationLog, NotificationPreference,
    Order, OrderItem, Product, PushSubscription,
)
from core.notifications import create_notification, visible_recipients_for
from core.services.notification_delivery import user_is_push_target
from operations.duty import (
    accept_duty_handover, assign_duty_coverage, request_duty_handover,
)
from operations.opening import set_staff_roster
from operations.services import open_business_day


@override_settings(
    DUTY_NOTIFICATION_ROUTING_ENABLED=True,
    PUSH_NOTIFICATIONS_ENABLED=True,
    PUSH_PROVIDER='webpush',
    VAPID_PUBLIC_KEY='public-key',
    VAPID_PRIVATE_KEY='private-key',
    VAPID_SUBJECT='mailto:ops@example.org',
)
class DutyAlertFollowupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username='followup-owner', phone='+963955993001',
            email='followup@example.org', password='pass',
        )
        self.tamer = User.objects.create_user(
            username='followup-tamer', phone='+963955993002',
            role=User.Role.CASHIER,
        )
        self.salwa = User.objects.create_user(
            username='followup-salwa', phone='+963955993003',
            role=User.Role.CASHIER,
        )
        self.provider = User.objects.create_user(
            username='followup-provider', phone='+963955993004',
            role=User.Role.INTERNET_PROVIDER,
        )
        for user in (self.owner, self.tamer, self.salwa, self.provider):
            NotificationPreference.objects.create(
                user=user, enable_browser_notifications=True,
            )
            PushSubscription.objects.create(
                user=user,
                endpoint=f'https://fcm.googleapis.com/fcm/send/followup-{user.pk}',
                p256dh=f'followup-key-{user.pk}',
                auth_secret=f'followup-auth-{user.pk}',
            )
        self.day = open_business_day(actor=self.owner)
        set_staff_roster(
            self.day, actor=self.owner,
            user_ids=[self.owner.pk, self.tamer.pk, self.salwa.pk],
        )
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        self.order = Order.objects.create()
        self.cat = Category.objects.create(name_ar='تجربة تغطية')
        self.product = Product.objects.create(
            category=self.cat, name_ar='شاي اختبار', price_syp=150,
        )
        self.kitchen, _ = PrepStation.objects.get_or_create(
            code='kitchen',
            defaults={'name_ar': 'المطبخ', 'station_type': 'kitchen'},
        )

    def _event(self, event_type='new_order', *, order_item=None, station=None):
        with self.captureOnCommitCallbacks(execute=True):
            event = create_notification(
                event_type, 'تنبيه يحتاج متابعة',
                order=self.order, order_item=order_item, target_station=station,
            )
        self.assertIsNotNone(event)
        return event

    def _pushed(self, event):
        return list(NotificationLog.objects.filter(
            notification_event=event, channel=NotificationLog.Channel.BROWSER,
        ).values_list('recipient_user_id', flat=True))

    def _assign(self, user, role='service'):
        with self.captureOnCommitCallbacks(execute=True):
            return assign_duty_coverage(
                self.day, responsibility=role, user_id=user.pk, actor=self.owner,
            )

    def _handover(self):
        request_duty_handover(
            self.day, responsibility='service', to_user_id=self.salwa.pk,
            actor=self.tamer,
        )
        with self.captureOnCommitCallbacks(execute=True):
            return accept_duty_handover(
                self.day, responsibility='service', actor=self.salwa,
            )

    def test_no_coverage_creates_manager_queue_without_new_task_rows(self):
        before_tasks = set(self.day.tasks.values_list('pk', flat=True))
        event = self._event()
        queue = unassigned_duty_alerts_for_manager(self.day, self.owner)
        self.assertEqual([item['event_id'] for item in queue], [event.pk])
        self.assertEqual(
            set(self.day.tasks.values_list('pk', flat=True)), before_tasks,
        )
        self.assertFalse(unassigned_duty_alerts_for_manager(self.day, self.provider))
        self.assertFalse(unassigned_duty_alerts_for_manager(self.day, self.tamer))

    def test_permission_revocation_hides_queue_even_for_admin(self):
        self._event()
        self.assertTrue(unassigned_duty_alerts_for_manager(self.day, self.owner))
        UserCapabilityOverride.objects.create(
            user=self.owner, capability='orders', allowed=False,
        )
        self.assertFalse(unassigned_duty_alerts_for_manager(self.day, self.owner))

    def test_manager_queue_clears_when_valid_holder_is_assigned(self):
        event = self._event()
        self.assertEqual(len(unassigned_duty_alerts_for_manager(self.day, self.owner)), 1)
        self._assign(self.tamer)
        self.assertEqual(unassigned_duty_alerts_for_manager(self.day, self.owner), [])
        self.assertIn(self.tamer.pk, self._pushed(event))

    def test_accepted_handover_only_requeues_still_new_order(self):
        self._assign(self.tamer)
        event = self._event()
        self.assertEqual(set(self._pushed(event)), {self.tamer.pk})
        self._handover()
        self.assertEqual(set(self._pushed(event)), {self.tamer.pk, self.salwa.pk})
        self.assertFalse(user_is_push_target(event, self.tamer))
        self.assertTrue(user_is_push_target(event, self.salwa))
        self.assertEqual(
            reenqueue_outstanding_after_duty_change(self.day.pk, 'service'), 0
        )
        self.assertEqual(len(self._pushed(event)), 2)

    def test_accepted_order_is_not_requeued_on_handover(self):
        self._assign(self.tamer)
        event = self._event()
        self.order.status = Order.Status.ACCEPTED
        self.order.save(update_fields=['status', 'updated_at'])
        self.assertFalse(event_has_outstanding_work(event))
        self._handover()
        self.assertEqual(self._pushed(event), [self.tamer.pk])

    def test_pending_handover_does_not_requeue_to_proposed_staff(self):
        self._assign(self.tamer)
        event = self._event()
        request_duty_handover(
            self.day, responsibility='service', to_user_id=self.salwa.pk,
            actor=self.tamer,
        )
        self.assertEqual(self._pushed(event), [self.tamer.pk])
        self.assertTrue(user_is_push_target(event, self.tamer))
        self.assertFalse(user_is_push_target(event, self.salwa))

    def test_preparation_requires_real_pending_item(self):
        item = OrderItem.objects.create(
            order=self.order, product=self.product, quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=150,
            prep_station=self.kitchen, prep_status=OrderItem.PrepStatus.NEW,
        )
        event = self._event(
            'new_prep_item', order_item=item, station=self.kitchen,
        )
        self.assertTrue(event_has_outstanding_work(event))
        item.prep_status = OrderItem.PrepStatus.ACCEPTED
        item.save(update_fields=['prep_status', 'updated_at'])
        self.assertFalse(event_has_outstanding_work(event))
        self.assertEqual(reenqueue_outstanding_after_duty_change(self.day.pk, 'kitchen'), 0)

    def test_resolved_or_cancelled_order_is_not_in_unassigned_queue(self):
        event = self._event()
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=['status', 'updated_at'])
        self.assertFalse(event_has_outstanding_work(event))
        self.assertFalse(unassigned_duty_alerts_for_manager(self.day, self.owner))

    def test_previous_business_day_alert_never_rebroadcasts_to_old_roles(self):
        event = self._event()
        event.duty_routing_business_date = self.day.business_date - timedelta(days=1)
        event.save(update_fields=['duty_routing_business_date', 'updated_at'])
        self.assertFalse(user_is_push_target(event, self.owner))
        self.assertFalse(user_is_push_target(event, self.tamer))
        self.assertFalse(
            visible_recipients_for(self.owner).filter(notification_event=event).exists()
        )
        self.assertEqual(unassigned_duty_alerts_for_manager(self.day, self.owner), [])
        with override_settings(DUTY_NOTIFICATION_ROUTING_ENABLED=False):
            self.assertTrue(user_is_push_target(event, self.owner))

    def test_only_flagged_events_appear_and_flag_off_is_noop(self):
        self._event()
        with override_settings(DUTY_NOTIFICATION_ROUTING_ENABLED=False):
            other = self._event()
            self.assertIsNone(other.duty_routing_business_date)
            self.assertFalse(unassigned_duty_alerts_for_manager(self.day, self.owner))
            self.assertEqual(
                reenqueue_outstanding_after_duty_change(self.day.pk, 'service'), 0,
            )
        self.assertEqual(len(unassigned_duty_alerts_for_manager(self.day, self.owner)), 1)

    def test_secret_and_manager_notice_are_never_in_unassigned_queue(self):
        self._event('guest_wifi_daily_pin')
        self._event('manager_approval_needed')
        self.assertEqual(unassigned_duty_alerts_for_manager(self.day, self.owner), [])

    def test_business_day_page_displays_queue_for_admin_not_regular_staff(self):
        self._event()
        self.client.force_login(self.owner)
        from django.urls import reverse
        response = self.client.get(reverse('staff_close_day'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تنبيهات تحتاج تغطية')
        self.assertContains(response, 'تنبيه يحتاج متابعة')
        self.client.force_login(self.tamer)
        from django.urls import reverse
        response = self.client.get(reverse('staff_close_day'))
        self.assertNotContains(response, 'تنبيه يحتاج متابعة')
