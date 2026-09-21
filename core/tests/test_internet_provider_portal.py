from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (
    ActivityLog,
    InternetEntitlement,
    InternetPackage,
    InternetPartner,
    InternetPartnerUser,
    InternetSession,
    Member,
)
from core.services.internet_access import create_entitlement, start_usage_session


@override_settings(SECURE_SSL_REDIRECT=False)
class InternetProviderPortalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.partner = InternetPartner.objects.create(
            name='Hub ISP', active=True, is_default=True, revenue_share_percent=30,
        )
        self.other_partner = InternetPartner.objects.create(
            name='Other ISP', active=True, revenue_share_percent=20,
        )
        self.user = User.objects.create_user(
            username='provider', phone='090-provider', password='provider-pass',
            role=User.Role.INTERNET_PROVIDER,
        )
        self.association = InternetPartnerUser.objects.create(
            partner=self.partner, user=self.user, can_view_customer_phone=False,
        )
        self.member = Member.objects.create(name_ar='عميل هَبّ', phone='0911111111')
        self.other_member = Member.objects.create(name_ar='عميل مخفي', phone='0922222222')
        self.package = InternetPackage.objects.create(
            name_ar='باقة هَبّ', code='hub-provider-package', price_syp=1000,
            access_mode=InternetPackage.AccessMode.ALLOWANCE,
            total_minutes_limit=300, partner=self.partner,
        )
        self.other_package = InternetPackage.objects.create(
            name_ar='باقة سرية', code='other-provider-package', price_syp=2000,
            access_mode=InternetPackage.AccessMode.ALLOWANCE,
            total_minutes_limit=300, partner=self.other_partner,
        )
        self.entitlement = create_entitlement(self.package, member=self.member, created_by=self.user)
        self.other_entitlement = create_entitlement(
            self.other_package, member=self.other_member, created_by=self.user,
        )
        self.client.force_login(self.user)

    def test_dashboard_and_legacy_route_are_provider_scoped(self):
        for name in ('internet_provider_dashboard', 'internet_partner_dashboard'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.entitlement.access_code)
            self.assertNotContains(response, self.other_entitlement.access_code)
            self.assertNotContains(response, self.other_member.name_ar)

    def test_provider_role_has_no_staff_workspace_access(self):
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_role_and_active_partner_association_are_both_required(self):
        no_partner = get_user_model().objects.create_user(
            username='no-partner', phone='090-no-partner', password='x',
            role='internet_provider',
        )
        self.client.force_login(no_partner)
        self.assertEqual(self.client.get(reverse('internet_provider_dashboard')).status_code, 403)

        wrong_role = get_user_model().objects.create_user(
            username='wrong-role', phone='090-wrong-role', password='x', role='waiter',
        )
        InternetPartnerUser.objects.create(partner=self.partner, user=wrong_role)
        self.client.force_login(wrong_role)
        self.assertEqual(self.client.get(reverse('internet_provider_dashboard')).status_code, 403)

    def test_provider_login_accepts_only_dedicated_provider_account(self):
        self.client.logout()
        response = self.client.post(reverse('internet_provider_login'), {
            'username': 'provider', 'password': 'provider-pass',
        })
        self.assertRedirects(response, reverse('internet_provider_dashboard'))

        waiter = get_user_model().objects.create_user(
            username='login-waiter', phone='090-login-waiter', password='waiter-pass', role='waiter',
        )
        self.client.logout()
        response = self.client.post(reverse('internet_provider_login'), {
            'username': waiter.username, 'password': 'waiter-pass',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'غير مخصص لبوابة مزوّد الإنترنت')

    def test_phone_visibility_is_controlled_by_association(self):
        response = self.client.get(reverse('internet_provider_members'))
        self.assertContains(response, self.member.name_ar)
        self.assertNotContains(response, self.member.phone)

        hidden_search = self.client.get(
            reverse('internet_provider_members'), {'q': self.member.phone},
        )
        self.assertNotContains(hidden_search, self.member.name_ar)

        self.association.can_view_customer_phone = True
        self.association.save(update_fields=['can_view_customer_phone'])
        response = self.client.get(reverse('internet_provider_members'))
        self.assertContains(response, self.member.phone)
        visible_search = self.client.get(
            reverse('internet_provider_members'), {'q': self.member.phone},
        )
        self.assertContains(visible_search, self.member.name_ar)

    def test_individual_deny_override_removes_management_action(self):
        UserCapabilityOverride.objects.create(
            user=self.user, capability='internet_manage_packages', allowed=False,
        )
        response = self.client.get(reverse(
            'internet_provider_package_edit', args=[self.package.public_code],
        ))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse('internet_provider_packages')).status_code, 200)

    def test_package_and_subscription_detail_fail_closed_across_partners(self):
        self.assertEqual(self.client.get(reverse(
            'internet_provider_package_edit', args=[self.other_package.public_code],
        )).status_code, 404)
        self.assertEqual(self.client.get(reverse(
            'internet_provider_subscription_detail', args=[self.other_entitlement.public_code],
        )).status_code, 404)

    def test_provider_can_suspend_and_resume_own_entitlement_with_audit(self):
        detail = reverse('internet_provider_subscription_detail', args=[self.entitlement.public_code])
        action = reverse('internet_provider_subscription_action', args=[self.entitlement.public_code])
        response = self.client.post(action, {'action': 'suspend', 'reason': 'اختبار تشغيلي'})
        self.assertRedirects(response, detail)
        self.entitlement.refresh_from_db()
        self.assertEqual(self.entitlement.status, InternetEntitlement.Status.SUSPENDED)
        self.assertTrue(ActivityLog.objects.filter(
            actor=self.user, action='internet.entitlement_suspended',
        ).exists())

        response = self.client.post(action, {'action': 'resume', 'reason': 'انتهاء الاختبار'})
        self.assertRedirects(response, detail)
        self.entitlement.refresh_from_db()
        self.assertEqual(self.entitlement.status, InternetEntitlement.Status.ACTIVE)
        self.assertTrue(ActivityLog.objects.filter(
            actor=self.user, action='internet.entitlement_resumed',
        ).exists())

    def test_provider_can_end_only_a_scoped_active_session(self):
        session = start_usage_session(self.entitlement, actor=self.user)
        other_session = start_usage_session(self.other_entitlement, actor=self.user)
        end_url = reverse('internet_provider_session_end', args=[session.public_code])
        response = self.client.post(end_url)
        self.assertRedirects(
            response, reverse('internet_provider_session_detail', args=[session.public_code]),
        )
        session.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.ENDED)
        self.assertEqual(self.client.post(reverse(
            'internet_provider_session_end', args=[other_session.public_code],
        )).status_code, 404)

    def test_provider_pages_render_and_reports_are_scoped(self):
        urls = (
            reverse('internet_provider_members'),
            reverse('internet_provider_member_detail', args=[self.member.public_code]),
            reverse('internet_provider_subscriptions'),
            reverse('internet_provider_sessions'),
            reverse('internet_provider_packages'),
            reverse('internet_provider_network'),
            reverse('internet_provider_reports'),
            reverse('internet_provider_settings'),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_default_provider_sees_package_less_mikrotik_operations(self):
        operational_member = Member.objects.create(
            name_ar='عميل اتصال أساسي', phone='0933333333',
        )
        session = InternetSession.objects.create(
            member=operational_member,
            start_time=timezone.now() - timedelta(minutes=12),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
        )

        sessions = self.client.get(reverse('internet_provider_sessions'))
        members = self.client.get(reverse('internet_provider_members'))
        dashboard = self.client.get(reverse('internet_provider_dashboard'))

        self.assertContains(sessions, session.public_code)
        self.assertContains(sessions, 'اتصال زائر')
        self.assertContains(members, operational_member.name_ar)
        self.assertContains(dashboard, operational_member.name_ar)

    def test_unassigned_manual_and_other_provider_sessions_remain_hidden(self):
        operational_member = Member.objects.create(
            name_ar='عميل لا يجب عرضه', phone='0944444444',
        )
        manual = InternetSession.objects.create(
            member=operational_member,
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MANUAL,
        )
        other_provider = get_user_model().objects.create_user(
            username='other-provider', phone='090-other-provider', password='x',
            role='internet_provider',
        )
        InternetPartnerUser.objects.create(partner=self.other_partner, user=other_provider)

        default_response = self.client.get(reverse('internet_provider_sessions'))
        self.assertNotContains(default_response, manual.public_code)
        self.client.force_login(other_provider)
        other_response = self.client.get(reverse('internet_provider_sessions'))
        self.assertNotContains(other_response, manual.public_code)

        mikrotik = InternetSession.objects.create(
            member=operational_member,
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
        )
        other_response = self.client.get(reverse('internet_provider_sessions'))
        self.assertNotContains(other_response, mikrotik.public_code)
