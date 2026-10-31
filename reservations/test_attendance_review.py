from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit, urljoin
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.settings_helpers import get_system_settings
from events.models import Event
from reservations.attendance import digest
from reservations.attendance_review import attendance_review_info
from reservations.models import EventAttendanceRequest as Request, Reservation
from reservations.services import event_reserved_party_size


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class StaffAttendanceReviewTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(title_ar='فعالية أصلية', starts_at=timezone.now()+timedelta(days=1),
                                         capacity=6, status='published', is_public=True, accepts_attendance_requests=True)
        self.actor = get_user_model().objects.create_user(username='reviewer', phone='+990080', role='admin', is_staff=True)
        self.client.force_login(self.actor)
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def row(self, event=None, **kwargs):
        event = event or self.event
        values = dict(event=event, event_uuid=event.uuid, event_title=event.title_ar, name='زائر تجريبي', phone='+963900000001',
                      party_size=3, client_key=uuid4(), owner_hash=digest(str(uuid4())), ip_hash=digest('synthetic'))
        values.update(kwargs)
        return Request.objects.create(**values)

    def path(self, row):
        return f'/staff/event-requests/{row.uuid}/'

    def reserve(self, party, status='confirmed'):
        return Reservation.objects.create(event=self.event, reservation_type='event', name='staff booking', phone='12345',
                                          party_size=party, status=status)

    def test_default_queue_is_pending_and_filters_combine(self):
        matching = self.row(name='سليم', phone='+963911111111')
        self.row(status='rejected', name='REJECTED_MARKER')
        other_event = Event.objects.create(title_ar='فعالية ثانية', starts_at=timezone.now()+timedelta(days=2))
        self.row(event=other_event, name='OTHER_EVENT_MARKER')
        default = self.client.get('/staff/event-requests/')
        self.assertContains(default, 'سليم')
        self.assertNotContains(default, 'REJECTED_MARKER')
        response = self.client.get('/staff/event-requests/', {'status': 'pending', 'event': str(self.event.uuid), 'q': 'سليم'})
        self.assertEqual(list(response.context['page']), [matching])
        self.assertNotContains(response, 'OTHER_EVENT_MARKER')
        self.assertContains(response, 'مسح البحث')

    def test_partial_phone_search_normalizes_arabic_and_persian_digits(self):
        matching = self.row(phone='+963911222333')
        self.row(phone='+963999999999')
        for query in ['٩١١٢٢٢', '۹۱۱۲۲۲', '+963 (911) 222']:
            response = self.client.get('/staff/event-requests/', {'q': query})
            self.assertEqual(list(response.context['page']), [matching])
        punctuation = self.client.get('/staff/event-requests/', {'q': '()'})
        self.assertEqual(len(punctuation.context['page']), 0)

    def test_title_search_uses_snapshot_and_current_name(self):
        row = self.row()
        self.event.title_ar = 'عنوان حديث'
        self.event.save()
        for query in ['أصلية', 'حديث']:
            response = self.client.get('/staff/event-requests/', {'q': query})
            self.assertEqual(list(response.context['page']), [row])

    def test_deleted_event_snapshot_remains_filterable(self):
        row = self.row()
        source_uuid = self.event.uuid
        self.event.delete()
        response = self.client.get('/staff/event-requests/', {'event': str(source_uuid), 'status': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['page']), [row])
        self.assertContains(response, row.event_title)

    def test_invalid_filters_return_validation_errors_without_results(self):
        self.row(name='PRIVATE_ROW_MARKER')
        for values in [{'event': 'bad-uuid'}, {'q': 'x'*81}, {'status': 'invalid'}]:
            response = self.client.get('/staff/event-requests/', values)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.context['page'].paginator.count, 0)
            self.assertNotContains(response, 'PRIVATE_ROW_MARKER', status_code=400)
        response = self.client.get('/staff/event-requests/', {'q': '<script>unsafe()</script>'})
        self.assertNotContains(response, '<script>unsafe()</script>')

    def test_pagination_preserves_all_filters(self):
        for _ in range(26):
            self.row()
        response = self.client.get('/staff/event-requests/', {'status': 'pending', 'event': str(self.event.uuid), 'q': 'زائر'})
        next_link = next(link for link in Links(response.content.decode()).links if 'page=2' in link)
        values = parse_qs(urlsplit(next_link).query)
        self.assertEqual(values, {'status': ['pending'], 'event': [str(self.event.uuid)], 'q': ['زائر'], 'page': ['2']})
        second = self.client.get(urljoin('/staff/event-requests/', next_link))
        self.assertEqual(len(second.context['page']), 1)

    def test_event_selector_reflects_canonical_and_unknown_uuid_filters(self):
        from uuid import UUID
        self.event.uuid = UUID('abcdefab-cdef-abcd-efab-cdefabcdefab')
        self.event.save()
        self.row()
        response = self.client.get('/staff/event-requests/', {'event': str(self.event.uuid).upper()})
        self.assertContains(response, f'<option value="{self.event.uuid}" selected')
        unknown = uuid4()
        empty = self.client.get('/staff/event-requests/', {'event': str(unknown)})
        self.assertEqual(empty.context['page'].paginator.count, 0)
        self.assertContains(empty, f'<option value="{unknown}" selected')

    def test_capacity_hint_matches_existing_reservation_policy(self):
        row = self.row()
        confirmed = self.reserve(2)
        self.reserve(2, status='pending')
        for status in ['cancelled', 'completed', 'no_show']:
            self.reserve(6, status=status)
        self.row(party_size=6)  # Public pending requests never consume seats.
        info = attendance_review_info(row)
        self.assertEqual((info.capacity, info.reserved, info.available), (6, 4, 2))
        self.assertFalse(info.can_approve)
        self.assertEqual(event_reserved_party_size(self.event.pk, exclude_reservation_id=confirmed.pk), 2)
        response = self.client.get(self.path(row))
        self.assertContains(response, 'لا تكفي')
        self.assertNotContains(response, 'value="approve"')
        self.assertContains(response, 'value="reject"')
        confirmed.status = 'cancelled'
        confirmed.save()
        self.assertTrue(attendance_review_info(row).can_approve)
        self.assertContains(self.client.get(self.path(row)), 'value="approve"')

    def test_event_closure_and_missing_source_explain_blocked_approval(self):
        row = self.row()
        for changes, message in [({'status': 'cancelled'}, 'ملغاة'), ({'is_public': False}, 'غير منشورة'),
                                 ({'accepts_attendance_requests': False}, 'أُغلق استقبال'),
                                 ({'starts_at': timezone.now()-timedelta(seconds=1)}, 'بدأ موعد')]:
            Event.objects.filter(pk=self.event.pk).update(status='published', is_public=True, accepts_attendance_requests=True,
                                                         starts_at=timezone.now()+timedelta(days=1))
            Event.objects.filter(pk=self.event.pk).update(**changes)
            response = self.client.get(self.path(row))
            self.assertContains(response, message)
            self.assertNotContains(response, 'value="approve"')
            self.assertContains(response, 'value="reject"')
        self.event.delete()
        response = self.client.get(self.path(row))
        self.assertContains(response, 'حُذفت الفعالية')
        self.assertNotContains(response, 'value="approve"')

    def test_capacity_change_after_get_is_rechecked_by_post(self):
        row = self.row(party_size=5)
        self.assertTrue(self.client.get(self.path(row)).context['review_info'].can_approve)
        self.reserve(2)
        result = self.client.post(self.path(row), {'action': 'approve'})
        self.assertEqual(result.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
        self.assertIsNone(row.reservation_id)
        self.assertEqual(Reservation.objects.count(), 1)
        current = self.client.get(self.path(row))
        self.assertFalse(current.context['review_info'].can_approve)
        self.assertEqual(current.context['review_info'].available, 4)

    def test_orphaned_approval_is_not_presented_as_ready_to_approve(self):
        row = self.row(status='approved')
        response = self.client.get(self.path(row))
        self.assertContains(response, 'الحجز المرتبط غير موجود')
        self.assertNotContains(response, 'value="approve"')

    def test_review_gets_are_read_only_and_do_not_lock_rows(self):
        row = self.row()
        with CaptureQueriesContext(connection) as queries:
            self.client.get('/staff/event-requests/', {'q': 'زائر', 'event': str(self.event.uuid)})
            self.client.get(self.path(row))
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        self.assertFalse(any('FOR UPDATE' in q['sql'] for q in queries))
        self.assertEqual(Reservation.objects.count(), 0)

    def test_queue_queries_do_not_grow_per_row(self):
        self.row()
        self.client.get('/staff/event-requests/')  # Warm the shared settings cache.
        with CaptureQueriesContext(connection) as small:
            self.client.get('/staff/event-requests/')
        for _ in range(27):
            self.row()
        with CaptureQueriesContext(connection) as large:
            response = self.client.get('/staff/event-requests/')
        self.assertEqual(len(response.context['page']), 25)
        self.assertEqual(len(large), len(small))

    def test_existing_capability_protects_search_and_review(self):
        row = self.row()
        user = get_user_model().objects.create_user(username='kitchen-review', phone='+990081', role='kitchen')
        self.client.force_login(user)
        for path in ['/staff/event-requests/?q=12345', self.path(row)]:
            response = self.client.get(path)
            self.assertIn(response.status_code, [302, 403])
            self.assertNotIn(row.phone, response.content.decode())
