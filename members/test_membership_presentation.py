"""Membership screens use entitlement balances, not the obsolete minute column."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import InternetEntitlement, InternetPackage, Member
from members.models import MembershipPlan, MembershipSubscription
from members.presentation import membership_subscription_rows


class MembershipInternetPresentationTests(TestCase):
    def setUp(self):
        self.at = timezone.now()
        self.member = Member.objects.create(name_ar='عضو العرض', phone='0944500101')
        self.plan = MembershipPlan.objects.create(code='internet-display', name_ar='باقة هَب')
        self.subscription = MembershipSubscription.objects.create(
            member=self.member, plan=self.plan, status='active',
            starts_at=self.at - timedelta(days=1), ends_at=self.at + timedelta(days=1),
            remaining_internet_minutes=9999)

    def entitlement(self, **overrides):
        fields = {
            'member': self.member, 'subscription': self.subscription,
            'status': InternetEntitlement.Status.ACTIVE,
            'access_mode': InternetPackage.AccessMode.ALLOWANCE,
            'activation_policy': InternetPackage.ActivationPolicy.ON_PURCHASE,
            'valid_from': self.at - timedelta(days=1),
            'valid_until': self.at + timedelta(days=1),
            'total_minutes_allowed': 600, 'minutes_used': 175,
        }
        fields.update(overrides)
        return InternetEntitlement.objects.create(**fields)

    def test_new_allowance_uses_entitlement_not_subscription_legacy_field(self):
        self.entitlement()
        summary = membership_subscription_rows([self.subscription], at=self.at)[0]
        self.assertEqual(summary['status_label'], 'فعّال')
        self.assertEqual(len(summary['internet']), 1)
        benefit = summary['internet'][0]
        self.assertEqual(benefit['remaining_minutes'], 425)
        self.assertEqual(benefit['source'], 'رصيد المخصصات')
        self.assertTrue(benefit['available'])

    def test_legacy_membership_credit_uses_subscription_field_once(self):
        self.entitlement(
            access_mode=InternetPackage.AccessMode.MEMBERSHIP_CREDIT,
            total_minutes_allowed=None, minutes_used=0)
        benefit = membership_subscription_rows([self.subscription], at=self.at)[0]['internet'][0]
        self.assertEqual(benefit['remaining_minutes'], 9999)
        self.assertEqual(benefit['source'], 'رصيد العضوية القديم')

    def test_expired_and_pending_entitlements_are_not_presented_as_available(self):
        self.entitlement(valid_until=self.at - timedelta(minutes=1))
        self.entitlement(status='pending', activation_policy='on_first_use')
        summary = membership_subscription_rows([self.subscription], at=self.at)[0]
        self.assertEqual([b['available'] for b in summary['internet']], [False, False])
        self.assertEqual(summary['internet'][0]['status_label'], 'غير متاح حالياً')
        self.assertEqual(summary['internet'][1]['status_label'], 'بانتظار التفعيل')

    def test_expired_membership_cannot_make_active_entitlement_available(self):
        self.entitlement()
        future = self.at + timedelta(days=2)
        summary = membership_subscription_rows([self.subscription], at=future)[0]
        self.assertEqual(summary['status_label'], 'منتهي')
        self.assertFalse(summary['internet'][0]['available'])

    def test_direct_purchase_is_not_mixed_into_membership_benefits(self):
        self.entitlement()
        self.entitlement(subscription=None, total_minutes_allowed=2000, minutes_used=1)
        benefits = membership_subscription_rows([self.subscription], at=self.at)[0]['internet']
        self.assertEqual(len(benefits), 1)
        self.assertEqual(benefits[0]['remaining_minutes'], 425)

    def test_staff_detail_and_account_show_same_authoritative_balance(self):
        self.entitlement()
        staff = get_user_model().objects.create_user(
            username='member-display-cashier', phone='0944500123',
            password='x', role='cashier')
        self.client.force_login(staff)
        result = self.client.get(reverse('staff_member_detail', args=[self.member.public_code]))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, '425 دقيقة غير مستهلكة')
        self.assertNotContains(result, '9999 دقيقة')

        self.client.logout()
        identity = SimpleNamespace(member=self.member, account=None,
                                   active_subscription=self.subscription)
        with patch('member_accounts.views.resolve_member_identity', return_value=identity):
            result = self.client.get(reverse('member_account_home'))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, '425 دقيقة')
        self.assertNotContains(result, '9999 دقيقة')

    def test_subscription_without_internet_benefits_has_no_fake_balance(self):
        summary = membership_subscription_rows([self.subscription], at=self.at)[0]
        self.assertEqual(summary['internet'], [])
