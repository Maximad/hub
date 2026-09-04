from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import StaffCapabilityOverride
from core.models import NotificationEvent, NotificationRecipient
from core.notifications import create_notification, link_for_event, visible_recipients_for


class NotificationPermissionVisibilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='notice-admin', password='pass', phone='+963710000001', role='admin'
        )
        self.cashier = User.objects.create_user(
            username='notice-cashier', password='pass', phone='+963710000002', role='cashier'
        )
        self.waiter = User.objects.create_user(
            username='notice-waiter', password='pass', phone='+963710000003', role='waiter'
        )
        self.kitchen = User.objects.create_user(
            username='notice-kitchen', password='pass', phone='+963710000004', role='kitchen'
        )

    def test_waiter_account_role_resolves_service_notification_role(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
        )
        recipient = NotificationRecipient.objects.create(
            notification_event=event,
            role='service',
        )
        self.assertTrue(visible_recipients_for(self.waiter).filter(pk=recipient.pk).exists())

    def test_individual_deny_removes_notification_for_otherwise_matching_role(self):
        StaffCapabilityOverride.objects.create(
            user=self.waiter,
            capability='orders',
            allowed=False,
        )
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_ORDER,
            title_ar='طلب جديد',
        )
        recipient = NotificationRecipient.objects.create(
            notification_event=event,
            role='service',
        )
        self.assertFalse(visible_recipients_for(self.waiter).filter(pk=recipient.pk).exists())

    def test_cashier_can_receive_cashier_station_prep_but_user_deny_blocks_it(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
            title_ar='تحضير',
        )
        recipient = NotificationRecipient.objects.create(
            notification_event=event,
            role='cashier',
        )
        self.assertTrue(visible_recipients_for(self.cashier).filter(pk=recipient.pk).exists())

        StaffCapabilityOverride.objects.create(
            user=self.cashier,
            capability='kitchen_board',
            allowed=False,
        )
        cashier = type(self.cashier).objects.get(pk=self.cashier.pk)
        self.assertFalse(visible_recipients_for(cashier).filter(pk=recipient.pk).exists())

    def test_kitchen_sees_prep_and_admin_keeps_full_in_app_visibility(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
            title_ar='تحضير',
        )
        recipient = NotificationRecipient.objects.create(
            notification_event=event,
            role='kitchen',
        )
        self.assertTrue(visible_recipients_for(self.kitchen).filter(pk=recipient.pk).exists())
        self.assertTrue(visible_recipients_for(self.admin).filter(pk=recipient.pk).exists())

    def test_ready_prep_alert_moves_from_station_operator_to_service(self):
        with self.captureOnCommitCallbacks(execute=False):
            event = create_notification(
                NotificationEvent.EventType.PREP_ITEM_READY,
                'عنصر جاهز',
            )
        self.assertIsNotNone(event)
        self.assertEqual(set(event.recipients.values_list('role', flat=True)), {'admin', 'service'})
        service_recipient = event.recipients.get(role='service')
        self.assertTrue(visible_recipients_for(self.waiter).filter(pk=service_recipient.pk).exists())
        self.assertFalse(visible_recipients_for(self.kitchen).filter(pk=service_recipient.pk).exists())
        self.assertEqual(link_for_event(event), '/staff/orders/')
