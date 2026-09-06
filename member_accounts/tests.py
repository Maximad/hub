from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Member
from member_accounts.identity import resolve_member_identity
from member_accounts.models import MemberAccount, MemberInvitation
from member_accounts.services import create_invitation
from members.models import MembershipPlan, MembershipSubscription
from members.services import resolve_member_from_request


@override_settings(
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
    MEMBER_DEVICE_COOKIE_SECURE=True,
)
class MemberAccountFlowTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(name_ar='عضو حساب', phone='0991000001')
        self.admin = get_user_model().objects.create_user(
            username='member-account-admin', phone='0991000002', password='x', role='admin'
        )

    def claim(self, invitation_member=None, phone='', name=''):
        invitation, raw = create_invitation(
            member=invitation_member,
            invited_phone=phone,
            invited_name=name,
            created_by=self.admin,
        )
        url = reverse('member_account_join', kwargs={'token': raw})
        preview = self.client.get(url)
        self.assertEqual(preview.status_code, 200)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.claimed_at)
        response = self.client.post(url, {'confirm': 'yes', 'name': name})
        return invitation, raw, response

    def test_existing_member_invitation_is_single_use_and_sets_trusted_device(self):
        invitation, raw, response = self.claim(invitation_member=self.member)
        self.assertRedirects(response, reverse('member_account_home'), fetch_redirect_response=False)
        invitation.refresh_from_db()
        account = self.member.login_account
        account.refresh_from_db()
        self.assertEqual(account.status, MemberAccount.Status.ACTIVE)
        self.assertIsNotNone(account.claimed_at)
        self.assertEqual(invitation.claimed_member, self.member)
        self.assertIsNotNone(invitation.claimed_at)
        self.assertEqual(self.member.device_tokens.filter(revoked_at__isnull=True).count(), 1)
        self.assertIn('hub_member_device', response.cookies)
        self.assertTrue(response.cookies['hub_member_device']['httponly'])
        self.assertTrue(response.cookies['hub_member_device']['secure'])

        repeated = self.client.post(
            reverse('member_account_join', kwargs={'token': raw}), {'confirm': 'yes'}
        )
        self.assertEqual(repeated.status_code, 400)
        self.assertEqual(self.member.device_tokens.count(), 1)

    def test_unbound_invitation_creates_member_and_avoids_duplicate_phone(self):
        invitation, _, response = self.claim(phone='0991000003', name='عضو مدعو')
        self.assertEqual(response.status_code, 302)
        created = Member.objects.get(phone='0991000003')
        invitation.refresh_from_db()
        self.assertEqual(invitation.claimed_member, created)
        self.assertTrue(created.login_account.is_claimed)

        existing = Member.objects.create(name_ar='موجود', phone='0991000004')
        second, raw = create_invitation(
            invited_phone='0991000004', invited_name='اسم آخر', created_by=self.admin
        )
        second_response = self.client.post(
            reverse('member_account_join', kwargs={'token': raw}),
            {'confirm': 'yes', 'name': 'اسم آخر'},
        )
        self.assertEqual(second_response.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.claimed_member, existing)
        self.assertEqual(Member.objects.filter(phone='0991000004').count(), 1)

    def test_account_identity_survives_membership_expiry_without_benefits(self):
        _, _, response = self.claim(invitation_member=self.member)
        self.client.cookies['hub_member_device'] = response.cookies['hub_member_device'].value
        plan = MembershipPlan.objects.create(code='expired-account-plan', name_ar='قديمة')
        MembershipSubscription.objects.create(
            member=self.member,
            plan=plan,
            starts_at=timezone.now() - timedelta(days=30),
            ends_at=timezone.now() - timedelta(days=1),
            status=MembershipSubscription.Status.ACTIVE,
        )

        request = self.client.get(reverse('member_account_home')).wsgi_request
        identity = resolve_member_identity(request)
        context = resolve_member_from_request(request)
        self.assertEqual(identity.member, self.member)
        self.assertIsNone(identity.active_subscription)
        self.assertEqual(context.member, self.member)
        self.assertIsNone(context.subscription)
        self.assertIsNone(context.plan)

        portal = self.client.get(reverse('member_account_home'))
        self.assertEqual(portal.status_code, 200)
        self.assertContains(portal, 'الحساب فعال')
        self.assertContains(portal, 'لا توجد عضوية فعالة حالياً')

        menu = self.client.get(reverse('menu_public'))
        self.assertEqual(menu.status_code, 200)
        self.assertEqual(menu.context['member_identity_context'].member, self.member)
        self.assertIsNone(menu.context['member_context'])
        self.assertContains(menu, reverse('member_account_home'))
        self.assertNotContains(menu, 'عضويتك مفعّلة')

    def test_active_membership_keeps_benefit_context_and_account_link(self):
        _, _, response = self.claim(invitation_member=self.member)
        self.client.cookies['hub_member_device'] = response.cookies['hub_member_device'].value
        plan = MembershipPlan.objects.create(code='active-account-plan', name_ar='فعالة')
        MembershipSubscription.objects.create(
            member=self.member,
            plan=plan,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=30),
            status=MembershipSubscription.Status.ACTIVE,
        )
        menu = self.client.get(reverse('menu_public'))
        self.assertEqual(menu.status_code, 200)
        self.assertEqual(menu.context['member_context'].member, self.member)
        self.assertEqual(menu.context['member_identity_context'].member, self.member)
        self.assertContains(menu, 'عضويتك مفعّلة')
        self.assertContains(menu, reverse('member_account_home'))

    def test_logout_revokes_only_current_device(self):
        _, _, response = self.claim(invitation_member=self.member)
        self.client.cookies['hub_member_device'] = response.cookies['hub_member_device'].value
        device = self.member.device_tokens.get()
        logout = self.client.post(reverse('member_account_logout'))
        self.assertRedirects(logout, reverse('menu_public'), fetch_redirect_response=False)
        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)
        self.assertEqual(logout.cookies['hub_member_device']['max-age'], 0)

    def test_staff_can_invite_existing_or_new_member_but_waiter_cannot(self):
        self.client.force_login(self.admin)
        existing_url = reverse('staff_member_invitation_existing', kwargs={'member_id': self.member.public_code})
        response = self.client.post(existing_url, {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/join/')
        self.assertEqual(MemberInvitation.objects.filter(target_member=self.member).count(), 1)

        new_url = reverse('staff_member_invitation_new')
        new_response = self.client.post(new_url, {'name': 'جديد', 'phone': '0991000005'})
        self.assertEqual(new_response.status_code, 200)
        self.assertContains(new_response, '/join/')
        self.assertFalse(Member.objects.filter(phone='0991000005').exists())

        waiter = get_user_model().objects.create_user(
            username='member-account-waiter', phone='0991000006', password='x', role='waiter'
        )
        self.client.force_login(waiter)
        self.assertEqual(self.client.get(new_url).status_code, 404)
