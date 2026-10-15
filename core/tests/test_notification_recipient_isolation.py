"""Regression contract for staff notification privacy and per-user read state.

Existing event/recipient records (including historical role broadcasts) remain
unchanged. Read receipts are personal and must not mutate delivery records.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import NotificationEvent, NotificationReadReceipt, NotificationRecipient
from core.notifications import (
    get_unread_grouped_notification_count,
    group_notification_recipients_for_user,
    mark_grouped_notification_read,
    visible_recipients_for,
)


class NotificationRecipientIsolationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.cashier_a = User.objects.create_user(
            username='notification-cashier-a', phone='+963955440001',
            role='cashier', password='pass',
        )
        self.cashier_b = User.objects.create_user(
            username='notification-cashier-b', phone='+963955440002',
            role='cashier', password='pass',
        )
        self.waiter_a = User.objects.create_user(
            username='notification-waiter-a', phone='+963955440003',
            role='waiter', password='pass',
        )
        self.waiter_b = User.objects.create_user(
            username='notification-waiter-b', phone='+963955440004',
            role='waiter', password='pass',
        )
        self.owner = User.objects.create_superuser(
            username='notification-owner', phone='+963955440005',
            email='notifications-owner@example.org', password='pass',
        )

    def event(self, event_type=NotificationEvent.EventType.NEW_ORDER, **kwargs):
        return NotificationEvent.objects.create(
            event_type=event_type, title_ar='تنبيه للاختبار', **kwargs
        )

    def unread_keys(self, user):
        return {
            card['group_key']
            for card in group_notification_recipients_for_user(user, unread_only=True)
        }

    def test_role_broadcast_read_is_independent_per_cashier(self):
        event = self.event()
        shared = NotificationRecipient.objects.create(
            notification_event=event, role='cashier',
        )
        key = f'event:{event.pk}'
        self.assertIn(key, self.unread_keys(self.cashier_a))
        self.assertIn(key, self.unread_keys(self.cashier_b))

        self.assertEqual(mark_grouped_notification_read(self.cashier_a, group_key=key), 1)
        self.assertNotIn(key, self.unread_keys(self.cashier_a))
        self.assertIn(key, self.unread_keys(self.cashier_b))
        self.assertEqual(get_unread_grouped_notification_count(self.cashier_a), 0)
        self.assertEqual(get_unread_grouped_notification_count(self.cashier_b), 1)
        shared.refresh_from_db()
        self.assertFalse(shared.is_read)
        self.assertIsNone(shared.delivered_at)
        self.assertEqual(
            NotificationReadReceipt.objects.filter(
                notification_recipient=shared, user=self.cashier_a
            ).count(), 1
        )

        self.assertEqual(mark_grouped_notification_read(self.cashier_b, group_key=key), 1)
        self.assertEqual(get_unread_grouped_notification_count(self.cashier_b), 0)
        self.assertEqual(NotificationReadReceipt.objects.filter(notification_recipient=shared).count(), 2)
        self.assertEqual(mark_grouped_notification_read(self.cashier_b, group_key=key), 0)

    def test_direct_recipient_cannot_leak_through_role_or_admin_view(self):
        event = self.event(event_type=NotificationEvent.EventType.CLOSE_DAY_FINALIZED)
        private = NotificationRecipient.objects.create(
            notification_event=event, user=self.cashier_a, role='cashier',
        )
        key = f'event:{event.pk}'
        self.assertIn(key, self.unread_keys(self.cashier_a))
        self.assertNotIn(key, self.unread_keys(self.cashier_b))
        self.assertNotIn(key, self.unread_keys(self.owner))
        self.assertEqual(
            mark_grouped_notification_read(self.cashier_b, recipient_id=private.pk), 0
        )
        self.assertEqual(mark_grouped_notification_read(self.owner, group_key=key), 0)
        self.assertFalse(NotificationReadReceipt.objects.filter(notification_recipient=private).exists())

    def test_superuser_can_see_broadcasts_but_not_other_staff_private_notes(self):
        event = self.event()
        broadcast = NotificationRecipient.objects.create(
            notification_event=event, role='service',
        )
        target = NotificationRecipient.objects.create(
            notification_event=event, user=self.cashier_a, role='cashier',
        )
        self.assertEqual(
            set(visible_recipients_for(self.owner).filter(notification_event=event).values_list('pk', flat=True)),
            {broadcast.pk},
        )
        self.assertNotIn(target.pk, set(
            visible_recipients_for(self.owner).values_list('pk', flat=True)
        ))

    def test_waiters_match_service_role_broadcast_without_private_leak(self):
        event = self.event(event_type=NotificationEvent.EventType.ORDER_EDITED)
        NotificationRecipient.objects.create(notification_event=event, role='service')
        self.assertIn(f'event:{event.pk}', self.unread_keys(self.waiter_a))
        self.assertIn(f'event:{event.pk}', self.unread_keys(self.waiter_b))
        self.assertNotIn(f'event:{event.pk}', self.unread_keys(self.cashier_a))

        private = self.event(event_type=NotificationEvent.EventType.ORDER_EDITED)
        NotificationRecipient.objects.create(
            notification_event=private, user=self.waiter_a, role='service',
        )
        self.assertIn(f'event:{private.pk}', self.unread_keys(self.waiter_a))
        self.assertNotIn(f'event:{private.pk}', self.unread_keys(self.waiter_b))

    def test_anonymous_and_inactive_users_have_no_visible_notifications(self):
        event = self.event()
        NotificationRecipient.objects.create(notification_event=event, role='cashier')
        self.assertFalse(visible_recipients_for(AnonymousUser()).exists())
        self.cashier_a.is_active = False
        self.cashier_a.save(update_fields=['is_active'])
        self.assertFalse(visible_recipients_for(self.cashier_a).exists())

    def test_missing_or_forged_read_key_never_clears_all_notifications(self):
        event = self.event()
        NotificationRecipient.objects.create(notification_event=event, role='cashier')
        self.assertEqual(mark_grouped_notification_read(self.cashier_a), 0)
        self.assertEqual(mark_grouped_notification_read(self.cashier_a, group_key='event:999999'), 0)
        self.assertIn(f'event:{event.pk}', self.unread_keys(self.cashier_a))

    def test_legacy_shared_read_flag_is_preserved_without_backfill(self):
        event = self.event()
        NotificationRecipient.objects.create(
            notification_event=event, role='cashier', is_read=True,
        )
        self.assertNotIn(f'event:{event.pk}', self.unread_keys(self.cashier_a))
        self.assertNotIn(f'event:{event.pk}', self.unread_keys(self.cashier_b))

    def test_guest_pin_expiration_and_individual_visibility_remain_intact(self):
        event = self.event(
            event_type=NotificationEvent.EventType.GUEST_WIFI_DAILY_PIN,
            message_ar='رمز الزوار للاختبار',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        NotificationRecipient.objects.create(
            notification_event=event, user=self.cashier_a, role='cashier',
        )
        NotificationRecipient.objects.create(
            notification_event=event, user=self.cashier_b, role='cashier',
        )
        key = f'event:{event.pk}'
        self.assertIn(key, self.unread_keys(self.cashier_a))
        self.assertIn(key, self.unread_keys(self.cashier_b))
        mark_grouped_notification_read(self.cashier_a, group_key=key)
        self.assertNotIn(key, self.unread_keys(self.cashier_a))
        self.assertIn(key, self.unread_keys(self.cashier_b))

        event.expires_at = timezone.now() - timedelta(seconds=1)
        event.save(update_fields=['expires_at', 'updated_at'])
        self.assertNotIn(key, self.unread_keys(self.cashier_b))
        self.assertEqual(mark_grouped_notification_read(self.cashier_b, group_key=key), 0)

    def test_mark_read_http_endpoint_is_scoped_to_logged_in_user(self):
        event = self.event()
        NotificationRecipient.objects.create(notification_event=event, role='cashier')
        self.client.force_login(self.cashier_a)
        response = self.client.post(reverse('staff_notifications_mark_read'), {
            'id': f'event:{event.pk}',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['updated'], 1)
        self.assertNotIn(f'event:{event.pk}', self.unread_keys(self.cashier_a))
        self.assertIn(f'event:{event.pk}', self.unread_keys(self.cashier_b))
