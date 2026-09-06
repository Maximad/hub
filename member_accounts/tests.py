from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Member
from member_accounts.delivery import LOC_MEM_OUTBOX
from member_accounts.identity import resolve_member_identity
from member_accounts.models import MemberAccount, MemberInvitation, MemberLoginChallenge
from member_accounts.services import create_invitation
from members.models import MembershipPlan, MembershipSubscription
from members.services import resolve_member_from_request


@override_settings(
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
    MEMBER_DEVICE_COOKIE_SECURE=True,
    MEMBER_LOGIN_DELIVERY_BACKEND='locmem',
    MEMBER_LOGIN_CODE_AGE=600,
    MEMBER_LOGIN_RESEND_SECONDS=60,
    MEMBER_LOGIN_RATE_WINDOW_SECONDS=900,
    MEMBER_LOGIN_MAX_REQUESTS_PER_WINDOW=5,
)
class MemberAccountFlowTests(TestCase):
    def setUp(self):
        LOC_MEM_OUTBOX.clear()
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

    def request_login(self, phone=None, next_path=''):
        response = self.client.post(
            reverse('member_account_login'),
            {'phone': phone or self.member.phone, 'next': next_path},
        )
        challenge = MemberLoginChallenge.objects.order_by('-created_at').first()
        return response, challenge

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

    def test_passwordless_login_verifies_phone_and_creates_trusted_device(self):
        response, challenge = self.request_login(next_path=reverse('menu_public'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('member_account_login_verify', kwargs={'challenge_id': challenge.uuid}),
        )
        self.assertEqual(challenge.member, self.member)
        self.assertEqual(challenge.delivery_status, MemberLoginChallenge.DeliveryStatus.SENT)
        self.assertEqual(len(LOC_MEM_OUTBOX), 1)

        code = LOC_MEM_OUTBOX[0]['code']
        verified = self.client.post(
            reverse('member_account_login_verify', kwargs={'challenge_id': challenge.uuid}),
            {'code': code},
        )
        self.assertRedirects(verified, reverse('menu_public'), fetch_redirect_response=False)
        self.assertIn('hub_member_device', verified.cookies)
        self.assertTrue(verified.cookies['hub_member_device']['httponly'])
        self.assertTrue(verified.cookies['hub_member_device']['secure'])
        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.consumed_at)
        account = self.member.login_account
        account.refresh_from_db()
        self.assertTrue(account.is_claimed)
        self.assertIsNotNone(account.phone_verified_at)
        self.assertEqual(self.member.device_tokens.filter(revoked_at__isnull=True).count(), 1)

    def test_unknown_phone_has_same_request_shape_and_receives_no_code(self):
        known_response, known_challenge = self.request_login()
        self.assertEqual(known_response.status_code, 302)
        self.assertIsNotNone(known_challenge.member)
        self.assertEqual(len(LOC_MEM_OUTBOX), 1)

        unknown_response, unknown_challenge = self.request_login(phone='0991999999')
        self.assertEqual(unknown_response.status_code, 302)
        self.assertIsNone(unknown_challenge.member)
        self.assertEqual(unknown_challenge.delivery_status, MemberLoginChallenge.DeliveryStatus.SKIPPED)
        self.assertEqual(len(LOC_MEM_OUTBOX), 1)
        verify = self.client.post(
            reverse('member_account_login_verify', kwargs={'challenge_id': unknown_challenge.uuid}),
            {'code': '000000'},
        )
        self.assertEqual(verify.status_code, 200)
        self.assertContains(verify, 'رمز التحقق غير صالح أو منتهي الصلاحية')

    def test_login_code_has_attempt_limit_and_is_single_use(self):
        _, challenge = self.request_login()
        code = LOC_MEM_OUTBOX[0]['code']
        url = reverse('member_account_login_verify', kwargs={'challenge_id': challenge.uuid})
        for _ in range(4):
            response = self.client.post(url, {'code': '999999' if code != '999999' else '888888'})
            self.assertEqual(response.status_code, 200)
        fifth = self.client.post(url, {'code': '999999' if code != '999999' else '888888'})
        self.assertEqual(fifth.status_code, 200)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 5)
        self.assertIsNotNone(challenge.consumed_at)
        rejected_real_code = self.client.post(url, {'code': code})
        self.assertEqual(rejected_real_code.status_code, 200)
        self.assertNotIn('hub_member_device', rejected_real_code.cookies)

    def test_login_resend_is_throttled_without_sending_new_code(self):
        first, first_challenge = self.request_login()
        self.assertEqual(first.status_code, 302)
        second, second_challenge = self.request_login()
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first_challenge.uuid, second_challenge.uuid)
        self.assertEqual(MemberLoginChallenge.objects.count(), 1)
        self.assertEqual(len(LOC_MEM_OUTBOX), 1)

    def test_locked_account_does_not_receive_login_code(self):
        account = MemberAccount.objects.create(member=self.member, status=MemberAccount.Status.LOCKED)
        response, challenge = self.request_login()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(challenge.member)
        self.assertEqual(len(LOC_MEM_OUTBOX), 0)
        account.refresh_from_db()
        self.assertEqual(account.status, MemberAccount.Status.LOCKED)

    def test_unauthenticated_member_home_redirects_to_login(self):
        response = self.client.get(reverse('member_account_home'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('member_account_login')))

    def test_external_next_destination_is_not_accepted(self):
        response = self.client.post(
            f"{reverse('member_account_login')}?next=https://attacker.example/",
            {'phone': self.member.phone, 'next': 'https://attacker.example/'},
        )
        self.assertEqual(response.status_code, 302)
        challenge = MemberLoginChallenge.objects.get()
        self.assertEqual(challenge.next_path, '')
