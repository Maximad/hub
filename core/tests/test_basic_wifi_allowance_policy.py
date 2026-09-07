from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import Category, HubVisit, HubVisitBrowserCredential, Order, OrderItem, Product
from internet.guest_wifi import (
    current_venue_code,
    guest_wifi_code_required,
    guest_wifi_daily_minutes_remaining,
    start_guest_wifi_session,
)
from internet.models import GuestWifiDailyAllowance, GuestWifiOrderBonus, GuestWifiPolicy


@override_settings(MIKROTIK_ENABLED=False)
class BasicWifiAllowancePolicyTests(TestCase):
    def setUp(self):
        self.policy = GuestWifiPolicy.objects.get(key='default')
        self.policy.enabled = True
        self.policy.require_venue_code = True
        self.policy.session_minutes = 120
        self.policy.daily_complimentary_minutes = 360
        self.policy.order_bonus_enabled = True
        self.policy.order_bonus_minutes = 120
        self.policy.qualifying_order_minimum_syp = 100
        self.policy.save()
        self.visit = HubVisit.objects.create(notes='wifi test')
        self.credential = HubVisitBrowserCredential.objects.create(
            visit=self.visit,
            token_hash='a' * 64,
        )
        self.factory = RequestFactory()

    def _request(self, code=None):
        return self.factory.post('/wifi/', {'venue_code': code or current_venue_code(self.policy)})

    def _start(self):
        return start_guest_wifi_session(
            request=self._request(),
            visit=self.visit,
            credential=self.credential,
            venue_code=current_venue_code(self.policy),
        )

    def _order(self, *, price=200, status=Order.Status.NEW):
        category = Category.objects.create(name_ar=f'فئة {price}')
        product = Product.objects.create(category=category, name_ar=f'منتج {price}', price_syp=price)
        order = Order.objects.create(visit=self.visit, status=status)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=1,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=price,
            line_total_syp_snapshot=price,
        )
        return order

    def test_first_daily_start_requires_code_and_grants_initial_minutes(self):
        self.assertTrue(guest_wifi_code_required(self.policy, credential=self.credential))
        session, created = self._start()
        self.assertTrue(created)
        allowance = GuestWifiDailyAllowance.objects.get(credential=self.credential)
        self.assertEqual(allowance.initial_minutes_granted, 120)
        self.assertEqual(session.authorized_minutes, 120)
        self.assertFalse(guest_wifi_code_required(self.policy, credential=self.credential))
        self.assertEqual(guest_wifi_daily_minutes_remaining(self.credential, self.policy), 120)

    def test_wrong_venue_code_is_rejected(self):
        with self.assertRaises(ValidationError):
            start_guest_wifi_session(
                request=self._request('0000'),
                visit=self.visit,
                credential=self.credential,
                venue_code='0000',
            )
        self.assertFalse(GuestWifiDailyAllowance.objects.filter(
            credential=self.credential,
            initial_minutes_granted__gt=0,
        ).exists())

    def test_new_order_does_not_reward_but_accepted_order_does(self):
        self._start()
        order = self._order(price=200)
        self.assertFalse(GuestWifiOrderBonus.objects.filter(order=order).exists())
        order.status = Order.Status.ACCEPTED
        order.save(update_fields=['status', 'updated_at'])
        bonus = GuestWifiOrderBonus.objects.get(order=order)
        self.assertEqual(bonus.minutes, 120)
        allowance = GuestWifiDailyAllowance.objects.get(credential=self.credential)
        self.assertEqual(allowance.order_bonus_minutes_granted, 120)
        self.assertEqual(guest_wifi_daily_minutes_remaining(self.credential, self.policy), 240)

    def test_order_rewards_are_clipped_by_daily_complimentary_cap(self):
        self._start()
        first = self._order(price=200)
        first.status = Order.Status.ACCEPTED
        first.save(update_fields=['status', 'updated_at'])
        second = self._order(price=250)
        second.status = Order.Status.ACCEPTED
        second.save(update_fields=['status', 'updated_at'])
        third = self._order(price=300)
        third.status = Order.Status.ACCEPTED
        third.save(update_fields=['status', 'updated_at'])
        allowance = GuestWifiDailyAllowance.objects.get(credential=self.credential)
        self.assertEqual(allowance.total_granted_minutes, 360)
        self.assertEqual(GuestWifiOrderBonus.objects.filter(revoked_at__isnull=True).count(), 2)

    def test_below_minimum_order_does_not_reward(self):
        self._start()
        order = self._order(price=50)
        order.status = Order.Status.ACCEPTED
        order.save(update_fields=['status', 'updated_at'])
        self.assertFalse(GuestWifiOrderBonus.objects.filter(order=order).exists())
