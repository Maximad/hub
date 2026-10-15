"""Operational accounts and guest Wi-Fi signage are separate workflows."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Category, HubVisit, InternetSession, NotificationEvent, NotificationRecipient,
    Order, OrderItem, Product,
)
from core.notifications import group_notification_recipients_for_user, link_for_event
from core.services.notification_delivery import push_route_for_event
from internet.guest_wifi import (
    _code_matches, current_venue_code, venue_code_for_slot, venue_code_slot,
)
from internet.models import GuestWifiPolicy
from operations.models import BusinessDayTask
from operations.services import current_business_date, open_business_day
from operations.tasks import sync_business_day_tasks


@override_settings(
    ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
    MIKROTIK_ENABLED=False, BUSINESS_DAY_CUTOFF_HOUR=4,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class GuestWifiOperationsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='pin-ops-admin', phone='+96319997941', password='unused', role='admin',
        )
        self.cashier = User.objects.create_user(
            username='pin-ops-cashier', phone='+96319997942', password='unused', role='cashier',
        )
        self.waiter = User.objects.create_user(
            username='pin-ops-waiter', phone='+96319997943', password='unused', role='waiter',
        )
        self.provider = User.objects.create_user(
            username='pin-ops-provider', phone='+96319997944',
            password='unused', role=User.Role.INTERNET_PROVIDER,
        )
        self.policy, _ = GuestWifiPolicy.objects.get_or_create(key='default')
        self.policy.enabled = True
        self.policy.require_venue_code = True
        self.policy.save(update_fields=['enabled', 'require_venue_code'])
        self.client.force_login(self.admin)

    def _session(self, visit, billing_mode):
        return InternetSession.objects.create(
            visit=visit, start_time=timezone.now(),
            status=InternetSession.Status.ACTIVE,
            billing_mode=billing_mode,
        )

    def test_free_only_accounts_are_not_cashier_cards_but_paid_metered_are(self):
        free = HubVisit.objects.create(notes='guest_wifi')
        self._session(free, InternetSession.BillingMode.FREE)
        empty = HubVisit.objects.create()
        paid_time = HubVisit.objects.create(guest_name='ضيف سريع', guest_phone='0999000101')
        self._session(paid_time, InternetSession.BillingMode.OPEN_METERED)

        category = Category.objects.create(name_ar='مشروبات التجربة')
        product = Product.objects.create(category=category, name_ar='شاي تجريبي', price_syp=150)
        billed = HubVisit.objects.create(guest_name='ضيف لديه طلب')
        order = Order.objects.create(visit=billed, status=Order.Status.READY)
        OrderItem.objects.create(
            order=order, product=product, quantity=1,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=150, line_total_syp_snapshot=150,
        )

        response = self.client.get(reverse('staff_home'))
        shown = {row['visit'].pk for row in response.context['visit_rows']}
        self.assertEqual(shown, {paid_time.pk, billed.pk})
        self.assertNotIn(free.pk, shown)
        self.assertNotIn(empty.pk, shown)
        self.assertContains(response, 'ضيف سريع')
        self.assertContains(response, 'إنترنت مدفوع قيد الاحتساب')
        self.assertContains(response, 'مطلوب التحصيل')
        self.assertEqual(response.context['workspace_stats']['open_visits'], 2)
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')
        self.assertTrue(InternetSession.objects.filter(visit=free).exists())

    def test_guest_pin_is_constant_during_business_day_and_rotates_at_cutoff(self):
        tz = timezone.get_current_timezone()
        day = timezone.localdate()
        at = lambda d, h, m: timezone.make_aware(datetime(d.year, d.month, d.day, h, m), tz)
        # Deliberately retain the legacy 4-hour configuration to demonstrate
        # the public PIN does NOT rotate during the same business day.
        self.policy.code_rotation_minutes = 240
        first = at(day, 5, 0)
        last = at(day + timedelta(days=1), 3, 59)
        next_day = at(day + timedelta(days=1), 4, 2)
        after_grace = at(day + timedelta(days=1), 4, 20)
        self.assertEqual(venue_code_slot(self.policy, first), venue_code_slot(self.policy, last))
        self.assertEqual(current_venue_code(self.policy, first), current_venue_code(self.policy, last))
        self.assertNotEqual(venue_code_slot(self.policy, first), venue_code_slot(self.policy, next_day))
        old = current_venue_code(self.policy, first)
        self.assertEqual(_code_matches(self.policy, old, at=next_day), venue_code_slot(self.policy, first))
        self.assertIsNone(_code_matches(self.policy, old, at=after_grace))

    def test_day_open_creates_one_board_task_and_one_in_app_pin_notification(self):
        today = current_business_date()
        day = open_business_day(actor=self.admin, business_date=today)
        task = day.tasks.get(fingerprint='wifi:board-pin')
        self.assertTrue(task.is_required)
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)
        self.assertEqual(task.responsibility_role, 'cashier')

        event = NotificationEvent.objects.get(event_type=NotificationEvent.EventType.GUEST_WIFI_DAILY_PIN)
        expected = venue_code_for_slot(self.policy, today.toordinal())
        self.assertIn(expected, event.message_ar)
        self.assertEqual(link_for_event(event), reverse('staff_home'))
        self.assertIsNone(push_route_for_event(event))
        self.assertFalse(NotificationRecipient.objects.filter(notification_event=event, user=self.provider).exists())
        self.assertTrue(NotificationRecipient.objects.filter(notification_event=event, user=self.cashier).exists())
        self.assertTrue(NotificationRecipient.objects.filter(notification_event=event, user=self.waiter).exists())

        cards = group_notification_recipients_for_user(self.cashier)
        self.assertTrue(any(card['message'].find(expected) >= 0 for card in cards))

        synced = sync_business_day_tasks(day, actor=self.admin)
        self.assertEqual(synced['created'], 0)
        self.assertEqual(day.tasks.filter(fingerprint='wifi:board-pin').count(), 1)
        self.assertEqual(
            NotificationEvent.objects.filter(
                event_type=NotificationEvent.EventType.GUEST_WIFI_DAILY_PIN,
            ).count(), 1,
        )
        display = self.client.get(reverse('staff_home'))
        self.assertContains(display, expected)
        self.assertContains(display, 'مهمة اليوم: تحديث اللوحة')

        done = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'status', 'task_id': task.pk, 'status': 'done',
        })
        self.assertEqual(done.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.DONE)
        display = self.client.get(reverse('staff_home'))
        self.assertContains(display, 'تم تحديث اللوحة')
        sync_business_day_tasks(day, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.DONE)

    def test_disabled_free_wifi_does_not_show_pin_or_create_board_task(self):
        self.policy.enabled = False
        self.policy.save(update_fields=['enabled'])
        day = open_business_day(actor=self.admin, business_date=current_business_date())
        self.assertFalse(day.tasks.filter(fingerprint='wifi:board-pin').exists())
        self.assertFalse(NotificationEvent.objects.filter(
            event_type=NotificationEvent.EventType.GUEST_WIFI_DAILY_PIN,
        ).exists())
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['guest_wifi_pin'], '')
        self.assertNotContains(response, 'رمز الإنترنت المجاني لليوم')
