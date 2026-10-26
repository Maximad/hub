"""Membership readiness must be informative and never charge before known-invalid activation."""
import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, FinancialAccount, Member, Order, Payment
from members.membership_sales import create_membership_sale
from members.models import MembershipBenefitRule, MembershipPlan, MembershipSubscription
from members.plan_readiness import assess_membership_plan


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class MembershipReadinessTests(TestCase):
    def setUp(self):
        self.cashier = get_user_model().objects.create_user(
            username='readiness-cashier', phone='096600089', role='cashier')
        self.member = Member.objects.create(name_ar='عضو تجريبي', phone='096600090')
        self.plan = MembershipPlan.objects.create(
            code='readiness', name_ar='عضوية اختبار', price_syp=1000,
            billing_period=MembershipPlan.BillingPeriod.MONTHLY)
        FinancialAccount.objects.create(
            code='cash:member-readiness', name_ar='صندوق اختبار العضوية',
            account_type='asset', scope='cashbox', is_active=True,
            negative_balance_policy='allow',
        )
        FinancialAccount.objects.create(
            code='revenue:member-readiness', name_ar='إيراد اختبار العضوية',
            account_type='revenue', scope='operating', is_active=True,
        )
        self.url = reverse('staff_member_subscribe', args=[self.member.public_code])
        self.client.force_login(self.cashier)

    def internet_rule(self, **overrides):
        values = dict(plan=self.plan,
                      benefit_type=MembershipBenefitRule.BenefitType.INTERNET_MINUTES,
                      value_integer=90,
                      max_concurrent_devices=1,
                      max_registered_devices=1)
        values.update(overrides)
        return MembershipBenefitRule.objects.create(**values)

    def sale(self, key):
        return create_membership_sale(
            member=self.member, plan=self.plan,
            payment_method=Payment.Method.CASH,
            actor=self.cashier, idempotency_key=key, starts_at=timezone.now())

    def test_invalid_device_rule_blocks_before_money_and_shows_staff_reason(self):
        rule = self.internet_rule(max_concurrent_devices=3, max_registered_devices=1)
        assessment = assess_membership_plan(self.plan)
        self.assertFalse(assessment['can_sell'])
        self.assertEqual(len(assessment['errors']), 1)
        with self.assertRaises(ValidationError):
            self.sale('bad-devices')
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          MembershipSubscription.objects.count()), (0, 0, 0))
        page = self.client.get(self.url)
        self.assertContains(page, 'الخطة تحتاج تصحيحاً قبل البيع')
        self.assertNotContains(page, '90 دقيقة إنترنت عند تفعيل العضوية')
        self.assertEqual(rule.plan, self.plan)

    def test_excessive_allocation_is_rejected_before_payment(self):
        self.internet_rule(commercial_allocation_syp=1001)
        with self.assertRaises(ValidationError):
            self.sale('bad-allocation')
        self.assertEqual(Order.objects.count(), 0)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(MembershipSubscription.objects.exists())

    def test_valid_config_allows_original_commercial_sale(self):
        self.internet_rule(commercial_allocation_syp=0)
        result = self.sale('valid-plan')
        self.assertTrue(result.created)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(result.subscription.gross_amount_syp, 1000)
        self.assertTrue(assess_membership_plan(self.plan)['can_sell'])

    def test_configured_only_rule_is_advisory_not_a_fake_automatic_benefit(self):
        MembershipBenefitRule.objects.create(
            plan=self.plan,
            benefit_type=MembershipBenefitRule.BenefitType.BOOKING_PRIORITY)
        summary = assess_membership_plan(self.plan)
        self.assertTrue(summary['can_sell'])
        self.assertEqual(summary['supported_benefits'], [])
        self.assertIn('أولوية الحجز', summary['configured_only'])
        page = self.client.get(self.url)
        self.assertContains(page, 'قواعد أخرى مُعدّة تحتاج مراجعة')

    def test_zero_and_over_100_percent_discounts_not_advertised(self):
        MembershipBenefitRule.objects.create(
            plan=self.plan,
            benefit_type=MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_PERCENT,
            scope_type='all_hub_products', value_decimal=150)
        info = assess_membership_plan(self.plan)
        self.assertEqual(info['supported_benefits'], [])
        self.assertTrue(info['warnings'])
        self.assertNotContains(self.client.get(self.url), 'خصم 150٪')

    def test_readiness_command_json_does_not_modify_database(self):
        self.internet_rule(max_concurrent_devices=2, max_registered_devices=1)
        before = (Order.objects.count(), MembershipSubscription.objects.count(),
                  ActivityLog.objects.count(), Payment.objects.count())
        out = StringIO()
        call_command('membership_readiness', '--json', stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report['counts']['plans'], 1)
        self.assertEqual(report['counts']['blocked'], 1)
        self.assertEqual(report['plans'][0]['code'], self.plan.code)
        self.assertFalse(report['plans'][0]['sellable_configuration'])
        self.assertEqual(before, (
            Order.objects.count(), MembershipSubscription.objects.count(),
            ActivityLog.objects.count(), Payment.objects.count(),
        ))
        with self.assertRaises(CommandError):
            call_command('membership_readiness', '--strict', stdout=StringIO())

    def test_audit_only_includes_offered_plans_by_default(self):
        MembershipPlan.objects.create(
            code='hidden-audit', name_ar='خطة مخفية',
            visible_to_staff=False, price_syp=300)
        out = StringIO()
        call_command('membership_readiness', '--json', stdout=out)
        self.assertEqual(json.loads(out.getvalue())['counts']['plans'], 1)
        all_out = StringIO()
        call_command('membership_readiness', '--all', '--json', stdout=all_out)
        self.assertEqual(json.loads(all_out.getvalue())['counts']['plans'], 2)
