from dataclasses import asdict
from datetime import datetime, timezone as dt_timezone
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.settings_helpers import get_system_settings
from events.models import Event
from reservations.attendance import digest
from reservations.attendance_tracking import OWNER_SESSION_KEY, owned_attendance_requests, public_attendance_item
from reservations.models import EventAttendanceRequest as Request, Reservation


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class AttendanceTrackingTests(TestCase):
    def setUp(self):
        self.source = Event.objects.create(title_ar='لقاء معلن', status='published', is_public=True,
                                           starts_at=datetime(2026, 11, 7, 12, tzinfo=dt_timezone.utc))
        self.owner = 'synthetic-owner-nonce-A'
        self.other_owner = 'synthetic-owner-nonce-B'
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def browser(self, owner=None):
        client = Client()
        if owner is not None:
            session = client.session
            session[OWNER_SESSION_KEY] = owner
            session.save()
        return client

    def row(self, owner=None, **kwargs):
        values = dict(event=self.source, event_uuid=self.source.uuid, event_title=self.source.title_ar,
                      name='PRIVATE_CONTACT_NAME', phone='+000000000000', party_size=2, status='withdrawn',
                      owner_hash=digest(owner or self.owner), ip_hash=digest('PRIVATE_IP'), client_key=uuid4(),
                      review_reason='PRIVATE_REVIEW_REASON')
        values.update(kwargs)
        return Request.objects.create(**values)

    def receipt(self, row):
        return f'/attendance/{row.uuid}/'

    def test_empty_browser_history_does_not_create_session_or_domain_data(self):
        self.row()
        client = Client()
        with CaptureQueriesContext(connection) as queries:
            response = client.get('/attendance/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لا توجد طلبات مرتبطة بهذا المتصفح')
        self.assertNotContains(response, 'لقاء معلن')
        self.assertEqual(response.context['page'].paginator.count, 0)
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))

    def test_only_own_history_and_receipts_are_accessible(self):
        own = self.row(event_title='OWN_REQUEST')
        other = self.row(owner=self.other_owner, event_title='OTHER_REQUEST')
        first, second = self.browser(self.owner), self.browser(self.other_owner)
        response = first.get('/attendance/')
        self.assertContains(response, 'OWN_REQUEST')
        self.assertNotContains(response, 'OTHER_REQUEST')
        self.assertEqual(first.get(self.receipt(other)).status_code, 404)
        self.assertEqual(second.get(self.receipt(own)).status_code, 404)
        self.assertNotContains(second.get('/attendance/'), 'OWN_REQUEST')
        bypass = Client().get('/attendance/', {'owner_hash': digest(self.owner), 'phone': own.phone, 'uuid': str(own.uuid)})
        self.assertEqual(bypass.context['page'].paginator.count, 0)

    def test_staff_login_does_not_grant_public_receipt_ownership(self):
        row = self.row()
        user = get_user_model().objects.create_user(username='tracking-admin', role='admin', is_staff=True, phone='+990071')
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get('/attendance/').context['page'].paginator.count, 0)
        self.assertEqual(client.get(self.receipt(row)).status_code, 404)

    def test_shared_presenter_excludes_contact_credentials_and_internal_review(self):
        row = self.row()
        item = public_attendance_item(owned_attendance_requests(digest(self.owner)).get(pk=row.pk))
        payload = str(asdict(item))
        response = self.browser(self.owner).get('/attendance/')
        for private in ['PRIVATE_CONTACT_NAME', row.phone, 'PRIVATE_REVIEW_REASON', row.owner_hash, row.ip_hash,
                        str(row.client_key), self.owner]:
            self.assertNotIn(private, payload)
            self.assertNotContains(response, private)

    def test_request_time_and_current_public_event_time_use_damascus(self):
        row = self.row()
        Request.objects.filter(pk=row.pk).update(created_at=datetime(2026, 10, 31, 11, 45, tzinfo=dt_timezone.utc))
        client = self.browser(self.owner)
        with timezone.override('America/New_York'):
            response = client.get(self.receipt(row))
            history = client.get('/attendance/')
        for page in [response, history]:
            for text in ['أُرسل الطلب', '31 / 10 / 2026', '14:45', 'الموعد المنشور حالياً', '7 / 11 / 2026', '15:00', '+03:00']:
                self.assertContains(page, text)
        self.assertContains(response, f'/events/{self.source.uuid}/')

    def test_private_cancelled_completed_and_deleted_sources_hide_current_data(self):
        row = self.row(event_title='ORIGINAL_PUBLIC_TITLE')
        client = self.browser(self.owner)
        for changes in [dict(is_public=False), dict(is_public=True, status='draft'),
                        dict(status='cancelled'), dict(status='completed')]:
            Event.objects.filter(pk=self.source.pk).update(
                title_ar='PRIVATE_NEW_TITLE', description_ar='PRIVATE_DESCRIPTION',
                starts_at=datetime(2045, 1, 2, 12, tzinfo=dt_timezone.utc), **changes)
            for path in ['/attendance/', self.receipt(row)]:
                response = client.get(path)
                self.assertContains(response, 'ORIGINAL_PUBLIC_TITLE')
                self.assertContains(response, 'المنشور غير متاح')
                for hidden in ['PRIVATE_NEW_TITLE', 'PRIVATE_DESCRIPTION', '2045', f'/events/{self.source.uuid}/']:
                    self.assertNotContains(response, hidden)
        self.source.delete()
        self.assertContains(client.get('/attendance/'), 'ORIGINAL_PUBLIC_TITLE')
        self.assertContains(client.get(self.receipt(row)), 'المنشور غير متاح')

    def test_public_rescheduling_is_labelled_as_current_without_inventing_historical_date(self):
        row = self.row()
        Event.objects.filter(pk=self.source.pk).update(starts_at=datetime(2026, 11, 8, 13, tzinfo=dt_timezone.utc))
        response = self.browser(self.owner).get(self.receipt(row))
        self.assertContains(response, 'الموعد المنشور حالياً')
        self.assertContains(response, '8 / 11 / 2026')
        self.assertContains(response, '16:00')
        self.assertNotContains(response, '7 / 11 / 2026')

    def test_history_includes_terminal_states_and_uses_canonical_reservation_status(self):
        row = self.row(status='approved')
        reservation = Reservation.objects.create(reservation_type='event', event=self.source,
                                                   name='staff', phone='12345', party_size=2, status='confirmed')
        row.reservation = reservation
        row.save()
        client = self.browser(self.owner)
        for status, text in [('confirmed', 'أكّد حجز الحضور'), ('cancelled', 'ألغى الفريق الحجز'),
                             ('completed', 'انتهى الحضور'), ('no_show', 'سُجّل عدم حضور')]:
            Reservation.objects.filter(pk=reservation.pk).update(status=status)
            self.assertContains(client.get('/attendance/'), text)
            self.assertContains(client.get(self.receipt(row)), text)
        reservation.delete()
        self.assertContains(client.get('/attendance/'), 'للتحقق من حالة الحجز')
        self.row(status='rejected')
        self.row(status='withdrawn')
        self.assertEqual(client.get('/attendance/').context['page'].paginator.count, 3)

    def test_pending_withdrawal_still_uses_existing_csrf_protected_receipt(self):
        row = self.row(status='pending')
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = self.owner
        session.save()
        history = client.get('/attendance/')
        self.assertContains(history, self.receipt(row))
        self.assertEqual(client.post(self.receipt(row), {'action': 'withdraw'}).status_code, 403)
        receipt = client.get(self.receipt(row))
        self.assertContains(receipt, 'كل طلبات حضوري')
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(self.receipt(row), {'action': 'withdraw', 'csrfmiddlewaretoken': token}).status_code, 302)
        self.assertContains(client.get('/attendance/'), 'سحب الزائر الطلب')

    def test_pagination_is_owned_and_stable_with_equal_timestamps(self):
        rows = [self.row() for _ in range(14)]
        for _ in range(5):
            self.row(owner=self.other_owner)
        at = datetime(2026, 10, 31, 12, tzinfo=dt_timezone.utc)
        Request.objects.filter(owner_hash=digest(self.owner)).update(created_at=at)
        client = self.browser(self.owner)
        first = client.get('/attendance/')
        second = client.get('/attendance/?page=2')
        self.assertEqual(first.context['page'].paginator.count, 14)
        self.assertEqual([item.url for item in first.context['items']], [self.receipt(row) for row in reversed(rows[2:])])
        self.assertEqual([item.url for item in second.context['items']], [self.receipt(row) for row in reversed(rows[:2])])

    def test_navigation_is_discoverable_without_showing_nonce_or_changing_menu_shell(self):
        self.row()
        client = self.browser(self.owner)
        home = client.get('/')
        self.assertContains(home, 'طلبات حضوري')
        self.assertContains(home, 'href="/attendance/"')
        self.assertNotContains(home, self.owner)
        self.assertNotContains(Client().get('/'), 'طلبات حضوري')
        self.assertNotContains(client.get('/menu/'), 'طلبات حضوري')

    def test_all_tracking_gets_are_private_read_only_and_reject_history_mutations(self):
        row = self.row()
        client = self.browser(self.owner)
        with CaptureQueriesContext(connection) as queries:
            for path in ['/attendance/', self.receipt(row)]:
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('Cookie', response['Vary'])
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                self.assertEqual(client.head(path).status_code, 200)
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        for method in ['post', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(client, method)('/attendance/', {'action': 'approve'}).status_code, 405)

    def test_history_query_count_is_bounded_per_page(self):
        self.row()
        client = self.browser(self.owner)
        client.get('/attendance/')
        with CaptureQueriesContext(connection) as small:
            client.get('/attendance/')
        for _ in range(15):
            self.row()
        with CaptureQueriesContext(connection) as large:
            response = client.get('/attendance/')
        self.assertEqual(len(response.context['items']), 12)
        self.assertEqual(len(large), len(small))

    def test_malformed_session_owner_does_not_expose_data_or_crash(self):
        row = self.row()
        for owner in [123, {'hash': digest(self.owner)}, []]:
            client = self.browser(owner)
            self.assertEqual(client.get('/attendance/').context['page'].paginator.count, 0)
            self.assertEqual(client.get(self.receipt(row)).status_code, 404)

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_release_gate_keeps_history_closed_and_navigation_hidden(self):
        row = self.row()
        client = self.browser(self.owner)
        self.assertEqual(client.get('/attendance/').status_code, 404)
        self.assertEqual(client.get(self.receipt(row)).status_code, 404)
        self.assertNotContains(client.get('/'), 'طلبات حضوري')
