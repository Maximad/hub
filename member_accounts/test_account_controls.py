from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, Category, Member, Order, OrderDiscount, OrderItem, Product
from core.settings_helpers import get_system_settings
from member_accounts.models import MemberAccount
from member_accounts.services import issue_trusted_device


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class MemberAccountControlTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(name_ar='Synthetic member', phone='0990000811')
        self.other = Member.objects.create(name_ar='Other member', phone='0990000812')
        for member in [self.member, self.other]:
            MemberAccount.objects.create(member=member, status='active', claimed_at=timezone.now())
        self.device, self.cookie = issue_trusted_device(self.member, 'Synthetic browser')
        self.client.cookies[settings.MEMBER_DEVICE_COOKIE_NAME] = self.cookie
        self.second_device, self.second_cookie = issue_trusted_device(self.member, 'Second browser')
        self.foreign_device, _ = issue_trusted_device(self.other, 'Other browser')
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def revoke_url(self, device):
        return reverse('member_account_revoke_device', args=[device.uuid])

    def test_revoke_is_post_only_owned_and_idempotent(self):
        url = self.revoke_url(self.second_device)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(self.revoke_url(self.foreign_device)).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.second_device.refresh_from_db()
        self.device.refresh_from_db()
        self.foreign_device.refresh_from_db()
        self.assertIsNotNone(self.second_device.revoked_at)
        self.assertIsNone(self.device.revoked_at)
        self.assertIsNone(self.foreign_device.revoked_at)
        self.assertEqual(ActivityLog.objects.filter(action='member_account.revoke_device').count(), 1)
        revoked_browser = Client()
        revoked_browser.cookies[settings.MEMBER_DEVICE_COOKIE_NAME] = self.second_cookie
        self.assertEqual(revoked_browser.get('/me/').status_code, 302)
        self.assertEqual(revoked_browser.post(self.revoke_url(self.device)).status_code, 404)

    def test_current_device_revoke_clears_cookie_and_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies[settings.MEMBER_DEVICE_COOKIE_NAME] = self.cookie
        url = self.revoke_url(self.device)
        self.assertEqual(client.post(url).status_code, 403)
        client.get('/me/')
        response = client.post(url, HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[settings.MEMBER_DEVICE_COOKIE_NAME]['max-age'], 0)
        self.assertIn('no-store', response['Cache-Control'])

    def make_order(self, member):
        product = Product.objects.create(category=Category.objects.create(name_ar='Synthetic'),
                                         name_ar='CURRENT_PRODUCT_NAME', price_syp=9999)
        order = Order.objects.create(member=member, notes='PRIVATE_STAFF_NOTE',
            delivery_phone='PRIVATE_PHONE', delivery_address='PRIVATE_ADDRESS', delivery_fee_syp=100)
        OrderItem.objects.create(order=order, product=product, quantity=2,
            product_name_ar_snapshot='HISTORICAL_ITEM', unit_price_syp_snapshot=1000,
            line_total_syp_snapshot=2300, estimated_line_cost_syp_snapshot=1777,
            cancellation_notes='PRIVATE_CANCELLATION')
        OrderDiscount.objects.create(order=order, amount_syp=200, reason='PRIVATE_REASON')
        return order

    def test_owned_order_uses_snapshots_and_totals_without_writes_or_private_fields(self):
        order = self.make_order(self.member)
        url = reverse('member_account_order_detail', args=[order.public_code])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['details']['total'], 2200)
        self.assertEqual(response.context['details']['items'][0]['total'], 2300)
        self.assertContains(response, 'HISTORICAL_ITEM')
        for private in ['CURRENT_PRODUCT_NAME', 'PRIVATE_STAFF_NOTE', 'PRIVATE_PHONE',
                        'PRIVATE_ADDRESS', 'PRIVATE_CANCELLATION', 'PRIVATE_REASON', '1777']:
            self.assertNotContains(response, private)
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertContains(self.client.get('/me/'), url)

    def test_order_ownership_cannot_be_bypassed_by_staff_or_member_parameters(self):
        order = self.make_order(self.other)
        url = reverse('member_account_order_detail', args=[order.public_code])
        self.assertEqual(self.client.get(url, {'member': self.other.pk}).status_code, 404)
        self.assertEqual(Client().get(url).status_code, 404)
        staff = get_user_model().objects.create_user(username='synthetic-account-admin', role='admin', phone='0990000813')
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(self.revoke_url(self.foreign_device)).status_code, 404)

    def test_locked_account_cannot_use_controls_or_order_detail(self):
        order = self.make_order(self.member)
        MemberAccount.objects.filter(member=self.member).update(status='locked')
        self.assertEqual(self.client.get(reverse('member_account_order_detail', args=[order.public_code])).status_code, 404)
        self.assertEqual(self.client.post(self.revoke_url(self.second_device)).status_code, 404)

    def test_home_is_not_cached_and_handoff_does_not_change_subscription(self):
        before = self.member.subscriptions.count()
        response = self.client.get('/me/')
        self.assertContains(response, 'id="membership-renewal"')
        self.assertContains(response, reverse('member_attendance_history'))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.member.subscriptions.count(), before)
