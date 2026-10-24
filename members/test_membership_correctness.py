"""Membership correctness across customer menu, staff POS, and staff status views."""

from datetime import timedelta
from decimal import Decimal
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Category, Member, OrderDiscount, Product
from core.views_legacy import _create_order_from_selected_items
from members.benefits import resolve_product_discount
from members.models import MembershipBenefitRule, MembershipPlan, MembershipSubscription
from members.services import evaluate_membership_benefit, get_active_member_context


class MembershipCorrectnessTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.member = Member.objects.create(name_ar='عضو تجريبي', phone='0944550123')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category, name_ar='شاي', price_syp=1000, is_available=True)

    def plan(self, code):
        return MembershipPlan.objects.create(code=code, name_ar=code)

    def rule(self, plan, percent=None, amount=None, **kwargs):
        if percent is not None:
            benefit_type = MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_PERCENT
            value = percent
        else:
            benefit_type = MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_FIXED
            value = amount
        return MembershipBenefitRule.objects.create(
            plan=plan, benefit_type=benefit_type, value_decimal=value,
            scope_type='all_hub_products', **kwargs)

    def subscribe(self, plan, *, starts=None, ends=None, status='active'):
        return MembershipSubscription.objects.create(
            member=self.member, plan=plan, status=status,
            starts_at=starts or self.now - timedelta(days=1),
            ends_at=ends or self.now + timedelta(days=30))

    def test_menu_and_pos_use_same_snapshot_discount_and_modifier_price(self):
        plan = self.plan('hub')
        rule = self.rule(plan, percent=25)
        subscription = self.subscribe(plan)
        context = get_active_member_context(self.member)
        # Two drinks at 1,200 each after a 200 modifier on the menu/POS.
        result = evaluate_membership_benefit(context, self.product, quantity=2, unit_price=1200)
        direct = resolve_product_discount(self.member, self.product, quantity=2, unit_price=1200)
        self.assertEqual(result.discount, 600)
        self.assertEqual(result.original_total, 2400)
        self.assertEqual(Decimal(result.discount), direct.discount)

        order = _create_order_from_selected_items(
            None, [(self.product, 2, '', [], 200)], [], member_context=context)
        self.assertEqual(order.subtotal_syp, 2400)
        self.assertEqual(order.discount_syp, 600)
        self.assertEqual(order.total_syp, 1800)
        snapshot = json.loads(OrderDiscount.objects.get(order=order).notes)[0]
        self.assertEqual(snapshot['source_rule_id'], rule.pk)
        self.assertEqual(snapshot['subscription_id'], str(subscription.uuid))
        self.assertEqual(snapshot['plan'], plan.name_ar)

    def test_new_rules_and_price_edits_do_not_change_purchased_benefits(self):
        plan = self.plan('snapshot')
        self.rule(plan, percent=20)
        self.subscribe(plan)
        self.rule(plan, percent=90)
        existing = plan.benefit_rules.order_by('pk').first()
        existing.value_decimal = 95
        existing.is_active = False
        existing.save(update_fields=('value_decimal', 'is_active'))
        self.assertEqual(evaluate_membership_benefit(
            get_active_member_context(self.member), self.product).discount, 200)

    def test_multiple_plans_choose_best_discount_without_stacking(self):
        old_plan = self.plan('old')
        new_plan = self.plan('new')
        best = self.rule(old_plan, percent=35)
        self.rule(new_plan, percent=10)
        self.subscribe(old_plan, starts=self.now - timedelta(days=2))
        self.subscribe(new_plan, starts=self.now - timedelta(days=1))
        context = get_active_member_context(self.member)
        self.assertEqual(context.plan, new_plan)  # display plan is latest
        result = evaluate_membership_benefit(context, self.product)
        self.assertEqual(result.discount, 350)
        self.assertEqual(result.benefit.definition['rule_id'], best.pk)
        self.assertEqual(result.benefit.subscription.plan, old_plan)

    def test_booking_and_non_product_rules_cannot_override_discount(self):
        plan = self.plan('typed')
        MembershipBenefitRule.objects.create(
            plan=plan, benefit_type='booking_priority',
            scope_type='all_hub_products', priority=1000)
        self.rule(plan, percent=20, priority=1)
        self.subscribe(plan)
        self.assertEqual(evaluate_membership_benefit(
            get_active_member_context(self.member), self.product).discount, 200)

    def test_fixed_discount_applies_per_unit_and_wins_over_lower_percent(self):
        fixed = self.plan('fixed')
        percent = self.plan('percent')
        self.rule(fixed, amount=250)
        self.rule(percent, percent=15)
        self.subscribe(fixed)
        self.subscribe(percent)
        result = evaluate_membership_benefit(
            get_active_member_context(self.member), self.product,
            quantity=2, unit_price=1200)
        self.assertEqual(result.discount, 500)
        self.assertEqual(result.final_total, 1900)

    def test_deactivating_plan_blocks_new_sales_not_existing_entitlement(self):
        plan = self.plan('not-on-sale')
        self.rule(plan, percent=20)
        subscription = self.subscribe(plan)
        plan.is_active = False
        plan.save(update_fields=('is_active',))
        self.assertEqual(get_active_member_context(self.member).subscription, subscription)
        self.assertEqual(evaluate_membership_benefit(
            get_active_member_context(self.member), self.product).discount, 200)

    def test_expired_active_record_not_shown_as_active_to_staff(self):
        plan = self.plan('expired')
        self.rule(plan, percent=20)
        self.subscribe(plan, ends=self.now - timedelta(seconds=1))
        self.assertIsNone(get_active_member_context(self.member))
        staff = get_user_model().objects.create_user(
            username='members-list-cashier', phone='0944550999',
            password='x', role='cashier')
        self.client.force_login(staff)
        response = self.client.get(reverse('staff_members'))
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context['rows'] if row['member'] == self.member)
        self.assertIsNone(row['active_subscription'])
