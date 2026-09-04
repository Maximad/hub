from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.permissions import user_has_capability
from catalog.models import PrepStation
from core.models import (
    Category,
    NotificationEvent,
    NotificationLog,
    NotificationPreference,
    Order,
    OrderItem,
    Product,
    PushSubscription,
)
from core.services.notification_delivery import enqueue_push_deliveries_for_event


PUSH_ENABLED = override_settings(
    PUSH_NOTIFICATIONS_ENABLED=True,
    PUSH_PROVIDER='webpush',
    VAPID_PUBLIC_KEY='public-key',
    VAPID_PRIVATE_KEY='private-key',
    VAPID_SUBJECT='mailto:ops@example.com',
)


@PUSH_ENABLED
class BartenderPrepRoutingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.bartender = User.objects.create_user(
            username='bar-operator',
            password='pass',
            phone='+963000007001',
            role='bartender',
        )
        self.cashier = User.objects.create_user(
            username='bar-cashier',
            password='pass',
            phone='+963000007002',
            role='cashier',
        )
        for user in (self.bartender, self.cashier):
            NotificationPreference.objects.create(
                user=user,
                enable_browser_notifications=True,
                notify_prep_items=True,
            )
            PushSubscription.objects.create(
                user=user,
                endpoint=f'https://fcm.googleapis.com/fcm/send/{user.username}',
                p256dh=f'p256dh-{user.username}',
                auth_secret=f'auth-{user.username}',
            )
        self.bar, _ = PrepStation.objects.get_or_create(
            code='bar',
            defaults={
                'name_ar': 'البار',
                'station_type': 'bar',
                'is_active': True,
            },
        )
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='شاي',
            price_syp=100,
            product_type=Product.ProductType.DRINK,
            item_type=Product.ItemType.BEVERAGE,
            prep_station_ref=self.bar,
            requires_preparation=True,
        )
        self.order = Order.objects.create()
        self.item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            product_name_en_snapshot='',
            unit_price_syp_snapshot=100,
            line_total_syp_snapshot=100,
            prep_station=self.bar,
            prep_status=OrderItem.PrepStatus.NEW,
        )

    def test_bartender_has_prep_capability(self):
        self.assertTrue(user_has_capability(self.bartender, 'kitchen_board'))

    def test_bar_prep_push_routes_to_bartender_not_cashier(self):
        event = NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.NEW_PREP_ITEM,
            title_ar='عنصر جديد',
            order=self.order,
            order_item=self.item,
            target_station=self.bar,
        )
        created = enqueue_push_deliveries_for_event(event)
        self.assertEqual(created, 1)
        self.assertEqual(
            set(
                NotificationLog.objects.filter(
                    channel=NotificationLog.Channel.BROWSER,
                ).values_list('recipient_user__username', flat=True)
            ),
            {'bar-operator'},
        )

    def test_bartender_can_open_bar_board_and_accept_item(self):
        self.client.force_login(self.bartender)
        board = self.client.get(reverse('staff_prep_station', kwargs={'station_code': 'bar'}))
        self.assertEqual(board.status_code, 200)
        self.assertContains(board, self.product.name_ar)

        response = self.client.post(
            reverse('staff_kitchen_item_status', kwargs={'item_id': self.item.pk}),
            {'action': 'accept'},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.ACCEPTED)
