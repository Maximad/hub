from datetime import timedelta

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Category, Member, OrderDiscount, Product
from members.models import MemberActivationToken, MemberDeviceToken, MembershipBenefitRule, MembershipPlan, MembershipSubscription
from members.services import create_activation_token, evaluate_membership_benefit, get_active_member_context, resolve_member_from_request


@override_settings(MEMBER_DEVICE_COOKIE_SECURE=True)
class MemberRecognitionTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(name_ar='عضو تجريبي', phone='0999999999')
        self.plan = MembershipPlan.objects.create(code='member', name_ar='الخطة', price_syp=0)
        self.subscription = MembershipSubscription.objects.create(
            member=self.member, plan=self.plan, starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=30), status='active')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(category=self.category, name_ar='قهوة', price_syp=1000, is_available=True)
        self.rule = MembershipBenefitRule.objects.create(plan=self.plan, product=self.product, discount_percent=20, priority=10)

    def activate(self):
        token, raw = create_activation_token(self.member)
        self.assertNotEqual(token.token_hash, raw)
        response = self.client.get(reverse('member_activate', kwargs={'token': raw}))
        return token, raw, response

    def test_activation_is_one_time_hashed_and_cookie_is_secure(self):
        token, raw, response = self.activate()
        token.refresh_from_db()
        self.assertIsNotNone(token.consumed_at)
        self.assertEqual(MemberDeviceToken.objects.count(), 1)
        device = MemberDeviceToken.objects.get()
        cookie = response.cookies['hub_member_device']
        self.assertTrue(cookie['httponly'])
        self.assertTrue(cookie['secure'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertNotIn(cookie.value.split('.', 1)[1], device.token_hash)
        self.assertEqual(self.client.get(reverse('member_activate', kwargs={'token': raw})).status_code, 302)
        self.assertEqual(MemberDeviceToken.objects.count(), 1)

    def test_valid_cookie_resolves_and_revocation_stops_recognition(self):
        _, _, response = self.activate()
        request = RequestFactory().get('/menu/')
        request.COOKIES['hub_member_device'] = response.cookies['hub_member_device'].value
        context = resolve_member_from_request(request)
        self.assertEqual(context.member, self.member)
        context.device.revoked_at = timezone.now()
        context.device.save(update_fields=['revoked_at'])
        self.assertIsNone(resolve_member_from_request(request))

    def test_engine_uses_priority_without_stacking_and_respects_exclusion(self):
        MembershipBenefitRule.objects.create(plan=self.plan, category=self.category, discount_percent=50, priority=1)
        result = evaluate_membership_benefit(get_active_member_context(self.member), self.product, 2)
        self.assertEqual(result.discount, 400)
        self.product.not_discountable = True
        self.product.save(update_fields=['not_discountable'])
        self.assertEqual(evaluate_membership_benefit(get_active_member_context(self.member), self.product).discount, 0)

    def test_public_order_is_authoritative_and_links_member(self):
        _, _, response = self.activate()
        self.client.cookies['hub_member_device'] = response.cookies['hub_member_device'].value
        response = self.client.post(reverse('menu_public'), {f'qty_{self.product.pk}': '2', 'member_id': '999'})
        order = self.member.orders.get()
        self.assertIsNone(order.table)
        self.assertEqual(order.subtotal_syp, 2000)
        self.assertEqual(order.discount_syp, 400)
        self.assertEqual(order.total_syp, 1600)
        self.assertEqual(OrderDiscount.objects.get(order=order).discount_type, 'member')

    def test_inactive_subscription_falls_back_to_normal_price(self):
        self.subscription.status = 'cancelled'
        self.subscription.save(update_fields=['status'])
        self.assertIsNone(get_active_member_context(self.member))

    def test_wrong_account_revokes_and_clears_cookie(self):
        _, _, activated = self.activate()
        self.client.cookies['hub_member_device'] = activated.cookies['hub_member_device'].value
        response = self.client.post(reverse('member_device_deactivate'))
        self.assertEqual(response.cookies['hub_member_device']['max-age'], 0)
        self.assertIsNotNone(MemberDeviceToken.objects.get().revoked_at)
