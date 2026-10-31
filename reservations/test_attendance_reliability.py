"""Regressions for deadlines across lock waits and read-only retry resolution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as ThreadEvent
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.models import AuditEvent
from events.models import Event
from reservations.attendance import digest, review_request, submit_request
from reservations.models import EventAttendanceRequest as Request, Reservation


class RequestFixture:
    def setUp(self):
        super().setUp()
        self.event = self.make_event()
        self.actor = get_user_model().objects.create_user(username='reviewer', role='admin', phone='+990090')

    def make_event(self):
        return Event.objects.create(title_ar='synthetic event', status='published', is_public=True, capacity=5,
                                   accepts_attendance_requests=True, starts_at=timezone.now()+timedelta(days=1))

    def submit(self, event=None, owner='owner', **kwargs):
        values = dict(event_uuid=(event or self.event).uuid, owner_hash=digest(owner), ip_hash=digest('ip'),
                      client_key=uuid4(), name='visitor', phone='12345678', party_size=1)
        values.update(kwargs)
        return submit_request(**values)


class AttendanceRetryTests(RequestFixture, TestCase):
    @override_settings(PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT=1, PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT=1)
    def test_active_request_resolution_does_not_consume_submission_budget(self):
        row = self.submit()
        resolved = self.submit()  # Fresh form key; existing request is not a new submission.
        self.assertEqual(resolved.pk, row.pk)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='event_attendance_requested').count(), 1)

    @override_settings(PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT=1, PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT=1)
    def test_other_browser_cannot_resolve_existing_request_when_budget_exhausted(self):
        from reservations.attendance import RequestRateLimited
        self.submit()
        with self.assertRaises(RequestRateLimited):
            self.submit(owner='another-browser')
        with self.assertRaises(RequestRateLimited):
            self.submit(event=self.make_event())
        self.assertEqual(Request.objects.count(), 1)

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                       PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT=1, PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT=1)
    def test_reopening_form_at_limit_redirects_to_owned_receipt(self):
        path = f'/events/{self.event.uuid}/request/'
        data = dict(client_key=str(uuid4()), name='visitor', phone='12345678', party_size=1)
        first = self.client.post(path, data)
        self.assertEqual(first.status_code, 302)
        second = self.client.post(path, dict(data, client_key=str(uuid4())))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second.url, first.url)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 0)


class AttendanceLockDeadlineTests(RequestFixture, TransactionTestCase):
    def run_after_deadline(self, operation):
        attempted = ThreadEvent()
        clock = [self.event.starts_at-timedelta(seconds=1)]

        def worker():
            close_old_connections()
            try:
                def observe(execute, sql, params, many, context):
                    if 'FROM "events_event"' in sql and 'FOR UPDATE' in sql:
                        attempted.set()
                    return execute(sql, params, many, context)
                with connection.execute_wrapper(observe):
                    try:
                        operation()
                    except ValidationError:
                        return 'closed'
                return 'accepted'
            finally:
                close_old_connections()

        with patch('django.utils.timezone.now', side_effect=lambda: clock[0]):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    Event.objects.select_for_update().get(pk=self.event.pk)
                    future = pool.submit(worker)
                    if not attempted.wait(timeout=10):
                        self.fail('Worker never reached the locked event query')
                    clock[0] = self.event.starts_at
                return future.result(timeout=10)

    def test_submission_cannot_cross_start_while_acquiring_event_lock(self):
        self.assertEqual(self.run_after_deadline(self.submit), 'closed')
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_approval_cannot_cross_start_while_acquiring_event_lock(self):
        row = self.submit()
        def approve():
            actor = get_user_model().objects.get(pk=self.actor.pk)
            review_request(row.uuid, actor=actor, action='approve')
        self.assertEqual(self.run_after_deadline(approve), 'closed')
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
        self.assertIsNone(row.reservation_id)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_same_retry_key_across_browsers_and_events_is_serialized(self):
        events = [self.event] + [self.make_event() for _ in range(5)]
        key = uuid4()
        barrier = Barrier(len(events))
        def submit(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    self.submit(event=events[index], owner=f'owner-{index}', ip_hash=digest(f'ip-{index}'), client_key=key)
                    return 'created'
                except PermissionDenied:
                    return 'denied'
                except IntegrityError:
                    return 'database-error'
                except ValidationError:
                    return 'validation-error'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(events)) as pool:
            results = list(pool.map(submit, range(len(events))))
        self.assertEqual(results.count('created'), 1)
        self.assertEqual(results.count('denied'), len(events)-1, results)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 0)
