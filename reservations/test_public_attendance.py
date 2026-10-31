from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditEvent, Order, Payment
from core.settings_helpers import get_system_settings
from core.views.event_attendance import OWNER_SESSION_KEY
from events.models import Event
from reservations.attendance import digest, review_request, submit_request, withdraw_request, RequestRateLimited
from reservations.models import EventAttendanceRequest as Request, Reservation
from reservations.services import change_reservation_status


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class PublicAttendanceTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(title_ar='لقاء معلن', status='published', is_public=True,
                                         starts_at=timezone.now()+timedelta(days=1), capacity=4, accepts_attendance_requests=True)
        self.actor = get_user_model().objects.create_user(username='reviewer', role='admin', is_staff=True, phone='+990000')
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def path(self, event=None):
        return f'/events/{(event or self.event).uuid}/request/'

    def data(self, **kwargs):
        return dict(dict(name='زائر', phone='+963900000001', party_size=2, client_key=str(uuid4())), **kwargs)

    def submit(self, owner='browser', **kwargs):
        return submit_request(event_uuid=self.event.uuid, owner_hash=digest(owner), ip_hash=digest('ip'),
                              **self.data(**kwargs, client_key=uuid4()))

    def web_submit(self, client=None, **kwargs):
        return (client or self.client).post(self.path(), self.data(**kwargs))

    def test_requests_are_opt_in_and_require_explicit_positive_capacity(self):
        event = Event.objects.create(title_ar='غير مفتوح', status='published', is_public=True, starts_at=timezone.now()+timedelta(days=1))
        self.assertFalse(event.accepts_attendance_requests)
        self.assertEqual(self.client.get(self.path(event)).status_code, 404)
        self.assertNotContains(self.client.get(f'/events/{event.uuid}/'), 'أرسل طلب حضور')
        event.accepts_attendance_requests = True
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_public_form_get_is_read_only_and_has_honest_terms(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.path())
        self.assertContains(response, 'لا يحجز مقعداً')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, '<h1>', count=1)
        self.assertContains(response, '<main ', count=1)
        self.assertNotIn(OWNER_SESSION_KEY, self.client.session)
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))

    def test_submission_does_not_hold_seats_collect_money_or_create_members(self):
        orders, payments = Order.objects.count(), Payment.objects.count()
        response = self.web_submit()
        self.assertEqual(response.status_code, 302)
        row = Request.objects.get()
        self.assertEqual(row.status, 'pending')
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(Order.objects.count(), orders)
        self.assertEqual(Payment.objects.count(), payments)
        self.assertTrue(AuditEvent.objects.filter(action='event_attendance_requested').exists())
        self.assertContains(self.client.get(response.url), 'لا يحجز مقعداً')
        self.assertNotContains(self.client.get(response.url), row.phone)

    def test_no_other_browser_or_phone_claim_can_access_or_withdraw_request(self):
        response = self.web_submit()
        stranger = Client()
        self.assertEqual(stranger.get(response.url).status_code, 404)
        self.assertEqual(stranger.post(response.url, {'action': 'withdraw'}).status_code, 404)
        self.assertEqual(stranger.get(response.url+'?phone=+963900000001').status_code, 404)
        other = self.web_submit(client=stranger)
        self.assertEqual(self.client.get(other.url).status_code, 404)
        self.assertEqual(stranger.get(response.url).status_code, 404)

    def test_exact_retry_is_idempotent_and_active_request_keeps_original_party(self):
        data = self.data()
        first = self.client.post(self.path(), data)
        self.assertEqual(self.client.post(self.path(), data).url, first.url)
        self.assertEqual(self.web_submit(party_size=1).url, first.url)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(Request.objects.get().party_size, 2)
        self.assertEqual(AuditEvent.objects.filter(action='event_attendance_requested').count(), 1)
        changed = dict(data, party_size=1)
        self.assertEqual(self.client.post(self.path(), changed).status_code, 409)

    def test_cross_owner_idempotency_key_never_grants_access(self):
        data = self.data()
        first = self.client.post(self.path(), data)
        stranger = Client()
        self.assertEqual(stranger.post(self.path(), data).status_code, 403)
        self.assertEqual(stranger.get(first.url).status_code, 404)
        self.assertEqual(Request.objects.count(), 1)

    def test_closed_private_draft_cancelled_and_ongoing_events_reject_requests(self):
        for changes in [dict(status='draft'), dict(status='cancelled'), dict(status='completed'),
                        dict(status='published', is_public=False), dict(is_public=True, accepts_attendance_requests=False),
                        dict(accepts_attendance_requests=True, starts_at=timezone.now()-timedelta(seconds=1))]:
            Event.objects.filter(pk=self.event.pk).update(**changes)
            self.assertEqual(self.client.get(self.path()).status_code, 404)
            self.assertIn(self.web_submit().status_code, [404, 409])
        self.assertEqual(Request.objects.count(), 0)

    def test_input_validation_honeypot_and_csrf(self):
        for data in [self.data(name=''), self.data(phone='javascript'), self.data(party_size=0),
                     self.data(party_size=11), self.data(client_key='invalid'), self.data(website='spam')]:
            self.assertEqual(self.client.post(self.path(), data).status_code, 400)
        self.assertEqual(Request.objects.count(), 0)
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(self.path(), self.data()).status_code, 403)
        response = csrf_client.get(self.path())
        token = csrf_client.cookies['csrftoken'].value
        result = csrf_client.post(self.path(), dict(self.data(), csrfmiddlewaretoken=token))
        self.assertEqual(result.status_code, 302)
        self.assertEqual(csrf_client.post(result.url, {'action': 'withdraw'}).status_code, 403)

    def test_arabic_phone_digits_are_normalized_and_titles_escaped(self):
        self.event.title_ar = '<script>secret()</script>'
        self.event.save()
        result = self.web_submit(phone='٠٩٦٣ ٩٠٠٠٠٠١')
        self.assertEqual(Request.objects.get().phone, '09639000001')
        response = self.client.get(result.url)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>secret()</script>')

    @override_settings(PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT=1)
    def test_rate_limit_survives_withdrawal_and_invalid_get_does_not_clear_it(self):
        response = self.web_submit()
        self.client.post(response.url, {'action': 'withdraw'})
        limited = self.web_submit()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(Request.objects.count(), 1)

    @override_settings(PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT=1)
    def test_ip_limit_applies_across_sessions_without_persisting_raw_ip(self):
        self.web_submit()
        self.assertEqual(self.web_submit(client=Client()).status_code, 429)
        row = Request.objects.get()
        self.assertEqual(len(row.ip_hash), 64)
        self.assertNotEqual(row.ip_hash, '127.0.0.1')

    def test_pending_withdrawal_is_idempotent_and_cannot_be_approved(self):
        response = self.web_submit()
        self.assertEqual(self.client.post(response.url, {'action': 'withdraw'}).status_code, 302)
        self.assertEqual(self.client.post(response.url, {'action': 'withdraw'}).status_code, 302)
        row = Request.objects.get()
        self.assertEqual(row.status, 'withdrawn')
        with self.assertRaises(ValidationError):
            review_request(row.uuid, actor=self.actor, action='approve')
        self.assertEqual(Reservation.objects.count(), 0)

    def test_staff_approval_creates_one_confirmed_reservation_without_payment(self):
        row = self.submit()
        reviewed = review_request(row.uuid, actor=self.actor, action='approve')
        again = review_request(row.uuid, actor=self.actor, action='approve')
        self.assertEqual(reviewed.reservation_id, again.reservation_id)
        self.assertEqual(Reservation.objects.count(), 1)
        reservation = Reservation.objects.get()
        self.assertEqual((reservation.status, reservation.party_size, reservation.deposit_syp), ('confirmed', 2, 0))
        self.assertEqual(reservation.created_by, self.actor)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.filter(action='event_attendance_approve').count(), 1)
        with self.assertRaises(ValidationError):
            withdraw_request(row.uuid, owner_hash=digest('browser'))

    def test_rejection_requires_internal_reason_and_never_leaks_it(self):
        result = self.web_submit()
        row = Request.objects.get()
        with self.assertRaises(ValidationError):
            review_request(row.uuid, actor=self.actor, action='reject')
        review_request(row.uuid, actor=self.actor, action='reject', reason='PRIVATE_REVIEW_REASON')
        response = self.client.get(result.url)
        self.assertContains(response, 'لم يعتمد الفريق')
        self.assertNotContains(response, 'PRIVATE_REVIEW_REASON')
        self.assertEqual(Reservation.objects.count(), 0)

    def test_approval_rechecks_capacity_and_publication_without_partial_writes(self):
        row = self.submit()
        Reservation.objects.create(reservation_type='event', event=self.event, name='staff', phone='1', party_size=3, status='confirmed')
        with self.assertRaises(ValidationError):
            review_request(row.uuid, actor=self.actor, action='approve')
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
        self.assertIsNone(row.reservation_id)
        self.assertEqual(Reservation.objects.count(), 1)
        self.event.is_public = False
        self.event.save()
        with self.assertRaises(ValidationError):
            review_request(row.uuid, actor=self.actor, action='approve')
        self.event.delete()
        with self.assertRaises(ValidationError):
            review_request(row.uuid, actor=self.actor, action='approve')

    def test_approval_failure_rolls_back_reservation_and_request(self):
        row = self.submit()
        with patch('reservations.attendance._audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                review_request(row.uuid, actor=self.actor, action='approve')
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
        self.assertEqual(Reservation.objects.count(), 0)

    def test_receipt_follows_existing_reservation_cancellation_and_missing_link(self):
        result = self.web_submit()
        row = review_request(Request.objects.get().uuid, actor=self.actor, action='approve')
        self.assertContains(self.client.get(result.url), 'أكّد حجز الحضور')
        change_reservation_status(row.reservation_id, 'cancelled', actor=self.actor)
        self.assertContains(self.client.get(result.url), 'ألغى الفريق الحجز')
        self.assertEqual(self.client.post(result.url, {'action': 'withdraw'}).status_code, 409)
        row.reservation.delete()
        self.assertContains(self.client.get(result.url), 'للتحقق من حالة الحجز')
        review_request(row.uuid, actor=self.actor, action='approve')
        self.assertEqual(Reservation.objects.count(), 0)

    def test_staff_capability_and_method_access(self):
        row = self.submit()
        path = f'/staff/event-requests/{row.uuid}/'
        self.assertEqual(self.client.get('/staff/event-requests/').status_code, 302)
        for role in ['kitchen', 'internet_provider']:
            user = get_user_model().objects.create_user(username=role, role=role, phone='+99000'+str(len(role)))
            self.client.force_login(user)
            with self.assertRaises(PermissionDenied):
                review_request(row.uuid, actor=user, action='approve')
            self.assertIn(self.client.post(path, {'action': 'approve'}).status_code, [302, 403])
            self.client.logout()
        self.client.force_login(self.actor)
        self.assertContains(self.client.get('/staff/event-requests/'), row.name)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post(path, {'action': 'approve'}).status_code, 302)
        self.assertEqual(Request.objects.get().status, 'approved')

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_global_release_gate_blocks_request_and_receipt(self):
        row = self.submit()
        for path in [self.path(), f'/attendance/{row.uuid}/']:
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path, self.data()).status_code, 404)


class AttendanceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.event = Event.objects.create(title_ar='capacity', status='published', is_public=True, capacity=3,
                                         starts_at=timezone.now()+timedelta(days=1), accepts_attendance_requests=True)
        self.actor = get_user_model().objects.create_user(username='reviewer', role='admin')

    def row(self, owner):
        return submit_request(event_uuid=self.event.uuid, owner_hash=digest(owner), ip_hash=digest('ip'),
                              client_key=uuid4(), name='visitor', phone='12345678', party_size=2)

    def test_two_approvals_cannot_overbook(self):
        rows = [self.row('one'), self.row('two')]
        barrier = Barrier(2)
        def approve(row):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=10)
                try:
                    review_request(row.uuid, actor=actor, action='approve')
                    return True
                except ValidationError:
                    return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, rows))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(Request.objects.filter(status='pending').count(), 1)

    def test_duplicate_parallel_submissions_create_one_request(self):
        key = uuid4()
        barrier = Barrier(2)
        def submit(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return submit_request(event_uuid=self.event.uuid, owner_hash=digest('same'), ip_hash=digest('ip'),
                                      client_key=key, name='visitor', phone='12345678', party_size=1).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_approval_and_withdrawal_race_has_one_terminal_outcome(self):
        row = self.row('same')
        barrier = Barrier(2)
        def change(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    if action == 'approve':
                        review_request(row.uuid, actor=get_user_model().objects.get(pk=self.actor.pk), action='approve')
                    else:
                        withdraw_request(row.uuid, owner_hash=digest('same'))
                    return True
                except ValidationError:
                    return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ['approve', 'withdraw']))
        self.assertEqual(sorted(results), [False, True])
        row.refresh_from_db()
        self.assertEqual(Reservation.objects.count(), 1 if row.status == 'approved' else 0)
        self.assertIn(row.status, ['approved', 'withdrawn'])
