from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import Member
from members.models import MemberAttribute, MembershipPlan, MembershipSubscription


class MembershipModelTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.member = Member.objects.create(name_ar='عضو', phone='0900000001')
        self.plan = MembershipPlan.objects.create(
            code='hub-member', name_ar='عضو هَب', name_en='Hub Member',
            billing_period=MembershipPlan.BillingPeriod.MONTHLY, price_syp=100_000,
        )

    def test_plan_and_activation_with_price_snapshot(self):
        subscription = MembershipSubscription.objects.create(
            member=self.member, plan=self.plan, starts_at=self.now,
        )
        self.assertEqual(subscription.status, MembershipSubscription.Status.PENDING)
        self.assertEqual(subscription.gross_amount_syp, 100_000)

        subscription.activate(at=self.now)
        self.assertTrue(subscription.is_active_at(self.now))
        self.assertEqual(subscription.activated_at, self.now)

        self.plan.price_syp = 150_000
        self.plan.save()
        subscription.refresh_from_db()
        self.assertEqual(subscription.gross_amount_syp, 100_000)

    def test_expiry_uses_end_as_exclusive_boundary(self):
        end = self.now + timedelta(days=1)
        subscription = MembershipSubscription.objects.create(
            member=self.member, plan=self.plan, status=MembershipSubscription.Status.ACTIVE,
            starts_at=self.now, ends_at=end,
        )
        self.assertTrue(subscription.is_active_at(end - timedelta(microseconds=1)))
        self.assertEqual(subscription.effective_status(end), MembershipSubscription.Status.EXPIRED)

    def test_freeze_temporary_freeze_and_unfreeze(self):
        subscription = MembershipSubscription.objects.create(
            member=self.member, plan=self.plan, status=MembershipSubscription.Status.ACTIVE,
            starts_at=self.now - timedelta(days=1),
        )
        until = self.now + timedelta(days=2)
        subscription.freeze(at=self.now, until=until)
        self.assertEqual(subscription.effective_status(self.now), MembershipSubscription.Status.FROZEN)
        self.assertTrue(subscription.is_active_at(until))

        subscription.unfreeze()
        self.assertEqual(subscription.status, MembershipSubscription.Status.ACTIVE)
        self.assertIsNone(subscription.frozen_at)
        self.assertIsNone(subscription.freeze_until)

    def test_cancellation(self):
        subscription = MembershipSubscription.objects.create(
            member=self.member, plan=self.plan, status=MembershipSubscription.Status.ACTIVE,
            starts_at=self.now - timedelta(days=1),
        )
        subscription.cancel('Member request', at=self.now)
        self.assertEqual(subscription.effective_status(self.now), MembershipSubscription.Status.CANCELLED)
        self.assertEqual(subscription.cancellation_reason, 'Member request')
        self.assertFalse(subscription.is_active_at(self.now))

    def test_member_can_hold_multiple_active_plans(self):
        second_plan = MembershipPlan.objects.create(code='hub-work', name_ar='عمل', price_syp=200_000)
        for plan in (self.plan, second_plan):
            MembershipSubscription.objects.create(
                member=self.member, plan=plan, status=MembershipSubscription.Status.ACTIVE,
                starts_at=self.now - timedelta(days=1),
            )
        self.assertEqual(self.member.subscriptions.filter(status='active').count(), 2)


class MemberAttributeTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.member = Member.objects.create(name_ar='عضو', phone='0900000002')

    def test_permanent_and_expiring_attributes(self):
        permanent = MemberAttribute.objects.create(
            member=self.member, code='founding_member', granted_at=self.now,
        )
        expiring = MemberAttribute.objects.create(
            member=self.member, code='mentor', granted_at=self.now,
            expires_at=self.now + timedelta(days=30),
        )
        self.assertTrue(permanent.is_active_at(self.now + timedelta(days=3650)))
        self.assertTrue(expiring.is_active_at(self.now + timedelta(days=29)))
        self.assertFalse(expiring.is_active_at(self.now + timedelta(days=30)))

    def test_overlapping_duplicate_attribute_is_rejected(self):
        MemberAttribute.objects.create(
            member=self.member, code='project_member', granted_at=self.now,
            expires_at=self.now + timedelta(days=30),
        )
        with self.assertRaises(ValidationError):
            MemberAttribute.objects.create(
                member=self.member, code='project_member',
                granted_at=self.now + timedelta(days=10),
            )

    def test_non_overlapping_attribute_history_is_allowed(self):
        MemberAttribute.objects.create(
            member=self.member, code='volunteer',
            granted_at=self.now - timedelta(days=30), expires_at=self.now,
        )
        current = MemberAttribute.objects.create(
            member=self.member, code='volunteer', granted_at=self.now,
        )
        self.assertTrue(current.is_active_at(self.now))
