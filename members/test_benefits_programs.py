from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import Category, Member, Order, Payment, Product
from members.benefits import (get_active_subscriptions, get_internet_benefits,
                              get_internet_member_pricing_eligibility,
                              get_workspace_allowance, has_booking_priority,
                              resolve_product_discount)
from members.models import (MembershipBenefitRule, MembershipPlan,
                            MembershipSubscription, Program, ProgramEnrollment)
from vendors.models import Vendor


class MembershipBenefitServiceTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.member = Member.objects.create(name_ar='عضو', phone='0933000001')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category, name_ar='قهوة', price_syp=1000,
            item_type=Product.ItemType.BEVERAGE)

    def plan_with(self, code, benefit_type, **fields):
        plan = MembershipPlan.objects.create(code=code, name_ar=code)
        MembershipBenefitRule.objects.create(
            plan=plan, benefit_type=benefit_type, scope_type=fields.pop('scope_type', 'all_hub_products'),
            **fields)
        return plan

    def subscribe(self, plan, status='active', starts=None, ends=None, **fields):
        return MembershipSubscription.objects.create(
            member=self.member, plan=plan, status=status,
            starts_at=starts or self.now - timedelta(days=1),
            ends_at=ends if ends is not None else self.now + timedelta(days=1), **fields)

    def test_active_grants_while_expired_and_cancelled_are_ignored(self):
        active = self.plan_with('active-plan', 'booking_priority')
        expired = self.plan_with('expired-plan', 'workspace_minutes', value_integer=99)
        cancelled = self.plan_with('cancelled-plan', 'workspace_minutes', value_integer=88)
        self.subscribe(active)
        self.subscribe(expired, ends=self.now - timedelta(seconds=1))
        self.subscribe(cancelled, status='cancelled')
        self.assertTrue(has_booking_priority(self.member, self.now))
        self.assertEqual(get_workspace_allowance(self.member, self.now), 0)

    def test_temporary_freeze_only_becomes_effective_after_freeze_until(self):
        plan = self.plan_with('frozen', 'booking_priority')
        subscription = self.subscribe(
            plan, status='frozen', freeze_until=self.now + timedelta(hours=1))
        self.assertEqual(get_active_subscriptions(self.member, self.now), [])
        self.assertEqual(get_active_subscriptions(self.member, self.now + timedelta(hours=2)), [subscription])

    def test_multiple_plans_coexist_and_allowances_are_additive(self):
        self.subscribe(self.plan_with('work-10', 'workspace_minutes', value_integer=10))
        self.subscribe(self.plan_with('work-20', 'workspace_minutes', value_integer=20))
        self.assertEqual(get_workspace_allowance(self.member, self.now), 30)

    def test_highest_fixed_discount_wins_without_stacking(self):
        self.subscribe(self.plan_with('fixed-100', 'product_discount_fixed', value_decimal=100))
        self.subscribe(self.plan_with('fixed-200', 'product_discount_fixed', value_decimal=200))
        result = resolve_product_discount(self.member, self.product, self.now)
        self.assertEqual(result.discount, Decimal('200'))
        self.assertEqual(result.final_price, Decimal('800'))

    def test_highest_percent_discount_wins_without_stacking(self):
        self.subscribe(self.plan_with('percent-10', 'product_discount_percent', value_decimal=10))
        self.subscribe(self.plan_with('percent-30', 'product_discount_percent', value_decimal=30))
        result = resolve_product_discount(self.member, self.product, self.now)
        self.assertEqual(result.discount, Decimal('300'))
        self.assertEqual(result.final_price, Decimal('700'))

    def test_fixed_vs_percent_uses_actual_price(self):
        self.subscribe(self.plan_with('fixed', 'product_discount_fixed', value_decimal=350))
        self.subscribe(self.plan_with('percent', 'product_discount_percent', value_decimal=30))
        self.assertEqual(resolve_product_discount(self.member, self.product, self.now).discount, 350)

    def test_vendor_product_excluded_by_default_and_explicit_scope_required(self):
        vendor = Vendor.objects.create(name_ar='شريك')
        self.product.vendor = vendor
        self.product.save(update_fields=['vendor'])
        self.subscribe(self.plan_with('vendor-default', 'product_discount_percent', value_decimal=50))
        self.assertEqual(resolve_product_discount(self.member, self.product, self.now).discount, 0)
        self.product.vendor = None
        self.product.save(update_fields=['vendor'])
        no_scope = self.plan_with('no-scope', 'product_discount_percent', value_decimal=80, scope_type='')
        self.subscribe(no_scope)
        # The existing explicitly scoped 50% benefit remains the only eligible rule.
        self.assertEqual(resolve_product_discount(self.member, self.product, self.now).discount, 500)

    def test_booking_workspace_and_internet_discovery_has_no_side_effect(self):
        self.subscribe(self.plan_with('booking', 'booking_priority'))
        self.subscribe(self.plan_with('workspace', 'workspace_minutes', value_integer=45, scope_type='workspace'))
        self.subscribe(self.plan_with('net-price', 'internet_member_price', scope_type='internet'))
        self.subscribe(self.plan_with('net-minutes', 'internet_minutes', value_integer=60, scope_type='internet'))
        self.assertTrue(has_booking_priority(self.member, self.now))
        self.assertEqual(get_workspace_allowance(self.member, self.now), 45)
        self.assertTrue(get_internet_member_pricing_eligibility(self.member, self.now))
        self.assertEqual(len(get_internet_benefits(self.member, self.now)), 2)
        self.assertFalse(self.member.internet_entitlements.exists())

    def test_subscription_snapshot_is_unchanged_by_later_plan_rule_edit(self):
        plan = self.plan_with('history', 'product_discount_percent', value_decimal=10)
        self.subscribe(plan)
        rule = plan.benefit_rules.get()
        rule.value_decimal = 90
        rule.save(update_fields=['value_decimal'])
        self.assertEqual(resolve_product_discount(self.member, self.product, self.now).discount, 100)


class ProgramEnrollmentTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(name_ar='ولي أمر', phone='0933000002')
        self.program = Program.objects.create(code='midmar', name_ar='مضمار', status='open')

    def test_create_program_and_free_member_enrollment(self):
        enrollment = ProgramEnrollment.objects.create(
            program=self.program, member=self.member, status='active', metadata={'funding': 'free'})
        self.assertEqual(enrollment.member, self.member)
        self.assertEqual(self.member.program_enrollments.get(), enrollment)

    def test_child_participant_without_member_is_supported_but_name_is_required(self):
        child = ProgramEnrollment(program=self.program, participant_name='طفل')
        child.full_clean()
        child.save()
        with self.assertRaises(ValidationError):
            ProgramEnrollment(program=self.program).full_clean()

    def test_paid_and_subscription_links_are_optional_and_preserved(self):
        plan = MembershipPlan.objects.create(code='linked', name_ar='عضوية')
        subscription = MembershipSubscription.objects.create(
            member=self.member, plan=plan, status='active', starts_at=timezone.now())
        order = Order.objects.create(member=self.member)
        payment = Payment.objects.create(order=order, amount_syp=100, method=Payment.Method.UNPAID)
        enrollment = ProgramEnrollment.objects.create(
            program=self.program, member=self.member, subscription=subscription,
            order=order, payment=payment)
        self.assertEqual((enrollment.subscription, enrollment.order, enrollment.payment),
                         (subscription, order, payment))

    def test_completion_cancellation_and_program_edits_preserve_history(self):
        completed = ProgramEnrollment.objects.create(program=self.program, member=self.member)
        cancelled = ProgramEnrollment.objects.create(program=self.program, participant_name='مشارك')
        completed.complete()
        cancelled.cancel()
        self.program.name_ar = 'مضمار المحدث'
        self.program.is_active = False
        self.program.save(update_fields=['name_ar', 'is_active'])
        completed.refresh_from_db()
        cancelled.refresh_from_db()
        self.assertEqual(completed.status, 'completed')
        self.assertIsNotNone(completed.completed_at)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertIsNotNone(cancelled.cancelled_at)
        self.assertEqual(self.program.enrollments.count(), 2)
