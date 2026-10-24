"""Staff membership plan preview must be accurate, non-mutating, and clear."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Category, Member, Product
from members.models import MembershipBenefitRule, MembershipPlan
from members.plan_presentation import plan_term_summary, staff_plan_presentations


class StaffMembershipPlanPreviewTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='membership-preview-cashier', phone='0911555411', role='cashier', password='test')
        self.member = Member.objects.create(name_ar='مشارك', phone='0911555412')
        self.plan = MembershipPlan.objects.create(
            code='hub-public', name_ar='عضو هَب', price_syp=50000,
            billing_period=MembershipPlan.BillingPeriod.MONTHLY,
            description_ar='اشتراك في مساحة هَب')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category, name_ar='شاي', price_syp=1000, is_available=True)
        self.url = reverse('staff_member_subscribe', args=[self.member.public_code])

    def test_preview_lists_only_active_operational_rules(self):
        MembershipBenefitRule.objects.create(
            plan=self.plan, product=self.product, priority=1,
            benefit_type='product_discount_percent', value_decimal=20)
        MembershipBenefitRule.objects.create(
            plan=self.plan, benefit_type='internet_minutes',
            value_integer=600)
        MembershipBenefitRule.objects.create(
            plan=self.plan, benefit_type='booking_priority', priority=9)
        MembershipBenefitRule.objects.create(
            plan=self.plan, benefit_type='event_discount_percent', value_decimal=10)
        MembershipBenefitRule.objects.create(
            plan=self.plan, benefit_type='product_discount_fixed',
            value_decimal=500, is_active=False)
        info = staff_plan_presentations([self.plan])[0]
        self.assertEqual(info['term'], 'شهر واحد من تاريخ البداية')
        self.assertIn('على شاي', info['benefits'][0])
        self.assertIn('خصم 20٪', info['benefits'][0])
        self.assertIn('600 دقيقة إنترنت عند تفعيل العضوية', info['benefits'])
        self.assertEqual(len(info['benefits']), 2)
        self.assertIn('أولوية الحجز', info['configured_only'])
        self.assertIn('نسبة خصم على فعالية', info['configured_only'])

    def test_purchase_form_shows_live_price_term_and_cautions(self):
        MembershipBenefitRule.objects.create(
            plan=self.plan, benefit_type='product_discount_percent', value_decimal=10)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '50000')
        self.assertContains(response, 'شهر واحد من تاريخ البداية')
        self.assertContains(response, 'خصم 10٪')
        self.assertContains(response, 'يُنشئ التجديد عملية بيع جديدة')
        self.assertEqual(response.context['plan_presentations'][0]['plan'].pk, self.plan.pk)

    def test_hidden_or_inactive_plans_not_advertised(self):
        hidden = MembershipPlan.objects.create(
            code='hidden-preview', name_ar='خاص', price_syp=12, visible_to_staff=False)
        inactive = MembershipPlan.objects.create(
            code='inactive-preview', name_ar='قديم', price_syp=33, is_active=False)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertNotContains(response, hidden.code)
        self.assertNotContains(response, inactive.code)
        self.assertEqual(len(response.context['plan_presentations']), 1)

    def test_unauthorized_staff_cannot_access_sale_form(self):
        regular = get_user_model().objects.create_user(
            username='membership-preview-kitchen', phone='0911555413', role='kitchen', password='test')
        self.client.force_login(regular)
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)

    def test_fixed_term_and_no_term_cannot_claim_monthly(self):
        self.plan.billing_period = MembershipPlan.BillingPeriod.FIXED_TERM
        self.assertEqual(plan_term_summary(self.plan), 'تُحدَّد نهاية الاشتراك عند الشراء')
        self.plan.billing_period = MembershipPlan.BillingPeriod.NONE
        self.assertEqual(plan_term_summary(self.plan), 'دون تاريخ انتهاء تلقائي')
        self.plan.term_unit = MembershipPlan.TermUnit.WEEK
        self.plan.term_value = 2
        self.assertIn('2 أسبوع', plan_term_summary(self.plan))

    def test_invalid_sale_post_preserves_preview_without_sale(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {
            'plan': self.plan.pk, 'payment_method': 'unpaid',
            'idempotency_key': 'invalid-preview-post',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'شهر واحد من تاريخ البداية')
        self.assertContains(response, 'عضو هَب')
        from members.models import MembershipSubscription
        self.assertFalse(MembershipSubscription.objects.exists())
