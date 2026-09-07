from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, HubVisit, Order, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings
from member_accounts.card import issue_member_card_token, resolve_member_card_token
from member_accounts.models import MemberAccount
from member_accounts.services import issue_trusted_device
from core.models import Member


@override_settings(
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
    MEMBER_DEVICE_COOKIE_SECURE=True,
    MEMBER_CARD_QR_MAX_AGE=90,
)
class MemberCardTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(name_ar='عضو البطاقة', phone='0992000001')
        self.account = MemberAccount.objects.create(
            member=self.member,
            status=MemberAccount.Status.ACTIVE,
            claimed_at=timezone.now(),
        )
        self.admin = get_user_model().objects.create_user(
            username='card-admin', phone='0992000002', password='x', role='admin'
        )
        self.waiter = get_user_model().objects.create_user(
            username='card-waiter', phone='0992000003', password='x', role='waiter'
        )
        self.kitchen = get_user_model().objects.create_user(
            username='card-kitchen', phone='0992000004', password='x', role='kitchen'
        )

    def tearDown(self):
        get_system_settings.cache_clear()

    def _recognize_member_browser(self):
        _device, cookie = issue_trusted_device(self.member, 'test browser')
        self.client.cookies['hub_member_device'] = cookie
        return cookie

    def test_member_card_token_is_short_lived_and_contains_no_device_credential(self):
        token = issue_member_card_token(self.member)
        card = resolve_member_card_token(token)
        self.assertIsNotNone(card)
        self.assertEqual(card.member, self.member)
        self.assertNotIn('hub_member_device', token)
        self.assertIsNone(resolve_member_card_token(token, max_age=-1))

    def test_locked_account_invalidates_existing_card_immediately(self):
        token = issue_member_card_token(self.member)
        self.account.status = MemberAccount.Status.LOCKED
        self.account.save(update_fields=['status', 'updated_at'])
        self.assertIsNone(resolve_member_card_token(token))

    def test_recognized_member_can_render_rotating_qr(self):
        self._recognize_member_browser()
        response = self.client.get(reverse('member_card_qr'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Member-Card-Max-Age'], '90')
        self.assertIn(b'<svg', response.content)

    def test_unrecognized_browser_cannot_render_member_qr(self):
        response = self.client.get(reverse('member_card_qr'))
        self.assertEqual(response.status_code, 401)

    def test_pos_staff_can_scan_and_attach_member_to_open_visit_and_orders(self):
        visit = HubVisit.objects.create()
        order = Order.objects.create(visit=visit)
        token = issue_member_card_token(self.member)
        url = reverse('staff_member_card_scan', kwargs={'token': token})

        self.client.force_login(self.waiter)
        preview = self.client.get(url)
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, self.member.name_ar)
        self.assertContains(preview, 'لا توجد عضوية فعالة حالياً')

        response = self.client.post(url, {'visit': str(visit.public_code)})
        self.assertRedirects(
            response,
            reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}),
            fetch_redirect_response=False,
        )
        visit.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(visit.member, self.member)
        self.assertEqual(order.member, self.member)
        self.assertTrue(ActivityLog.objects.filter(
            action='member_account.card_attached_to_visit',
            actor=self.waiter,
        ).exists())

    def test_scan_never_overwrites_another_member_identity(self):
        other = Member.objects.create(name_ar='عضو آخر', phone='0992000005')
        visit = HubVisit.objects.create(member=other)
        order = Order.objects.create(visit=visit, member=other)
        token = issue_member_card_token(self.member)
        url = reverse('staff_member_card_scan', kwargs={'token': token})

        self.client.force_login(self.admin)
        response = self.client.post(url, {'visit': str(visit.public_code)})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'مرتبطة بعضو آخر', status_code=400)
        visit.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(visit.member, other)
        self.assertEqual(order.member, other)

    def test_non_pos_staff_cannot_resolve_member_card(self):
        token = issue_member_card_token(self.member)
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_member_card_scan', kwargs={'token': token}))
        self.assertEqual(response.status_code, 404)

    def test_table_qr_adds_visit_context_without_replacing_member_identity(self):
        SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()
        room = Room.objects.create(name_ar='قاعة')
        table = TableArea.objects.create(room=room, name_ar='طاولة 1')
        member_cookie = self._recognize_member_browser()
        url = reverse('menu_table', kwargs={'qr_token': table.qr_token})

        landing = self.client.get(url)
        self.assertEqual(landing.status_code, 200)
        created = self.client.post(url, {'visit_action': 'create'})
        self.assertEqual(created.status_code, 302)
        visit = HubVisit.objects.get(table=table)
        self.assertEqual(visit.member, self.member)
        self.assertEqual(self.client.cookies['hub_member_device'].value, member_cookie)

        reopened = self.client.get(url)
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(self.client.cookies['hub_member_device'].value, member_cookie)
