from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditEvent, Member
from core.settings_helpers import get_system_settings
from events.models import Event
from member_accounts.models import MemberAccount
from members.models import MemberDeviceToken
from reservations.attendance import claim_request, digest, withdraw_request
from reservations.attendance_tracking import OWNER_SESSION_KEY
from reservations.models import EventAttendanceRequest as Request


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class AttendanceAccountTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(title_ar='SYNTHETIC_EVENT', status='published', is_public=True,
                                          starts_at=timezone.now() + timedelta(days=1))
        self.member = Member.objects.create(name_ar='Synthetic member', phone='0990000711')
        self.other = Member.objects.create(name_ar='Other member', phone='0990000712')
        self.account = MemberAccount.objects.create(member=self.member, status='active', claimed_at=timezone.now())
        self.other_account = MemberAccount.objects.create(member=self.other, status='active', claimed_at=timezone.now())
        self.owner = 'synthetic-browser-owner'
        self.row = Request.objects.create(event=self.event, event_uuid=self.event.uuid,
            event_title=self.event.title_ar, name='PRIVATE_CONTACT', phone=self.member.phone,
            party_size=2, status='pending', owner_hash=digest(self.owner), ip_hash=digest('synthetic-ip'),
            client_key=uuid4(), review_reason='PRIVATE_REVIEW')
        self.url = f'/attendance/{self.row.uuid}/'
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def browser(self, member=None, owner=None, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        if owner:
            session = client.session
            session[OWNER_SESSION_KEY] = owner
            session.save()
        if member:
            secret = 'synthetic-device-' + str(uuid4())
            device = MemberDeviceToken.objects.create(member=member, token_hash=digest(secret))
            client.cookies[settings.MEMBER_DEVICE_COOKIE_NAME] = f'{device.uuid}.{secret}'
        return client

    def test_explicit_claim_is_idempotent_and_visible_on_another_account_device(self):
        browser = self.browser(self.member, self.owner)
        with CaptureQueriesContext(connection) as queries:
            response = browser.get(self.url)
        self.assertContains(response, 'value="claim"')
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        self.row.refresh_from_db()
        self.assertIsNone(self.row.account_id)
        self.assertEqual(browser.post(self.url, {'action': 'claim'}).status_code, 302)
        audit_count = AuditEvent.objects.filter(action='event_attendance_account_claimed').count()
        self.assertEqual(browser.post(self.url, {'action': 'claim'}).status_code, 302)
        self.assertEqual(AuditEvent.objects.filter(action='event_attendance_account_claimed').count(), audit_count)
        self.row.refresh_from_db()
        self.assertEqual(self.row.account_id, self.account.pk)
        self.assertEqual(self.row.status, 'pending')
        self.assertIsNone(self.row.reservation_id)
        other_device = self.browser(self.member)
        history = other_device.get('/me/attendance/')
        self.assertContains(history, self.event.title_ar)
        self.assertIn('no-store', history['Cache-Control'])
        self.assertEqual(history['X-Robots-Tag'], 'noindex, nofollow')
        response = other_device.get(self.url)
        self.assertEqual(response.status_code, 200)
        for private in [self.row.name, self.row.phone, self.row.review_reason, self.row.owner_hash, str(self.row.client_key)]:
            self.assertNotContains(response, private)
            self.assertNotContains(history, private)

    def test_phone_match_and_staff_login_do_not_claim_or_reveal_browser_requests(self):
        from django.contrib.auth import get_user_model
        member_browser = self.browser(self.member)
        self.assertNotContains(member_browser.get('/me/attendance/'), self.event.title_ar)
        self.assertEqual(member_browser.get(self.url).status_code, 404)
        self.assertEqual(member_browser.post(self.url, {'action': 'claim', 'owner_hash': self.row.owner_hash}).status_code, 404)
        staff = get_user_model().objects.create_user(username='synthetic-admin', role='admin', is_staff=True, phone='0990000713')
        client = self.browser(owner=self.owner)
        client.force_login(staff)
        self.assertEqual(client.post(self.url, {'action': 'claim'}).status_code, 404)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.account_id)

    def test_claim_removes_browser_only_access_and_cannot_be_reassigned(self):
        claim_request(self.row.uuid, owner_hash=digest(self.owner), account_id=self.account.pk)
        for client in [self.browser(owner=self.owner), self.browser(self.other, self.owner), self.browser(self.other)]:
            self.assertEqual(client.get(self.url).status_code, 404)
            for action in ['claim', 'withdraw']:
                self.assertEqual(client.post(self.url, {'action': action, 'account_id': self.account.pk}).status_code, 404)
            self.assertNotContains(client.get('/attendance/'), self.event.title_ar)
        with self.assertRaises(PermissionDenied):
            claim_request(self.row.uuid, owner_hash=digest(self.owner), account_id=self.other_account.pk)
        with self.assertRaises(Request.DoesNotExist):
            withdraw_request(self.row.uuid, owner_hash=digest(self.owner))
        self.assertNotContains(self.browser(self.other).get('/me/attendance/'), self.event.title_ar)
        self.assertEqual(self.browser(self.member).post(self.url, {'action': 'withdraw'}).status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'withdrawn')

    def test_claim_requires_csrf_and_authenticated_browser_possession(self):
        client = self.browser(self.member, self.owner, csrf=True)
        self.assertEqual(client.post(self.url, {'action': 'claim'}).status_code, 403)
        client.get(self.url)
        self.assertEqual(client.post(self.url, {'action': 'claim'},
                                    HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code, 302)
        self.assertEqual(Client().get('/me/attendance/').status_code, 302)

    def test_revoked_expired_and_locked_credentials_cannot_read_claimed_requests(self):
        claim_request(self.row.uuid, owner_hash=digest(self.owner), account_id=self.account.pk)
        for mode in ['revoked', 'expired', 'locked']:
            client = self.browser(self.member, self.owner)
            device = self.member.device_tokens.latest('pk')
            if mode == 'revoked':
                device.revoked_at = timezone.now()
            elif mode == 'expired':
                device.expires_at = timezone.now() - timedelta(seconds=1)
            else:
                self.account.status = 'locked'
                self.account.save(update_fields=['status'])
            device.save()
            self.assertEqual(client.get(self.url).status_code, 404)
            self.assertEqual(client.get('/me/attendance/').status_code, 302)


class AttendanceClaimConcurrencyTests(TransactionTestCase):
    def test_competing_accounts_cannot_both_claim_the_same_browser_request(self):
        accounts = [MemberAccount.objects.create(
            member=Member.objects.create(name_ar='Synthetic', phone=f'099000072{i}'),
            status='active', claimed_at=timezone.now()) for i in range(2)]
        owner = digest('synthetic-shared-browser')
        row = Request.objects.create(event_uuid=uuid4(), event_title='Synthetic', name='Synthetic',
                                     phone='0990000720', owner_hash=owner, ip_hash=digest('synthetic-ip'),
                                     client_key=uuid4())
        barrier = Barrier(2)

        def attempt(account):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                claim_request(row.uuid, owner_hash=owner, account_id=account.pk)
                return account.pk
            except PermissionDenied:
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [result for result in pool.map(attempt, accounts) if result is not None]
        self.assertEqual(len(winners), 1)
        row.refresh_from_db()
        self.assertEqual(row.account_id, winners[0])
        self.assertEqual(AuditEvent.objects.filter(action='event_attendance_account_claimed').count(), 1)
