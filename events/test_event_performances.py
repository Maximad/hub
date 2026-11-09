"""Focused acceptance tests for multi-show and bounded recurrence events."""
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventPerformance, EventTicketType
from events.scheduling import add_performances, cancel_performance
from reservations.attendance import review_request, submit_request
from reservations.importing import import_event_bookings, validate_rows
from reservations.models import Reservation
from reservations.services import create_reservation
from reservations.ticketing import set_event_ticket_allocations

DAMASCUS = ZoneInfo('Asia/Damascus')


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   PUBLIC_HOMEPAGE_ENABLED=True)
class EventPerformanceTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='performance-admin', phone='+963991000100',
            password='staff', role='admin',
        )
        start = timezone.now() + timedelta(days=9)
        self.event = Event.objects.create(
            title_ar='ليلة ستاند أب', starts_at=start,
            ends_at=start + timedelta(hours=2), capacity=2,
            status=Event.Status.PUBLISHED, is_public=True,
            accepts_attendance_requests=True,
        )
        self.early = EventPerformance.objects.create(
            event=self.event, starts_at=start,
            ends_at=start + timedelta(hours=2), capacity=2,
        )
        self.late = EventPerformance.objects.create(
            event=self.event, starts_at=start + timedelta(hours=3),
            ends_at=start + timedelta(hours=5), capacity=2,
        )

    def book(self, show, *, size=1, phone='+963991234567'):
        return create_reservation(Reservation(
            reservation_type='event', event=self.event, performance=show,
            name='ضيف', phone=phone, party_size=size,
            status=Reservation.Status.CONFIRMED, created_by=self.staff,
        ))

    def test_two_shows_share_event_but_not_capacity(self):
        first = self.book(self.early, size=2)
        second = self.book(self.late, size=2)
        self.assertEqual(Event.objects.count(), 1)
        self.assertNotEqual(first.effective_starts_at, second.effective_starts_at)
        with self.assertRaises(ValidationError):
            self.book(self.early)
        with self.assertRaises(ValidationError):
            self.book(self.late)
        with self.assertRaises(ValidationError):
            create_reservation(Reservation(
                reservation_type='event', event=self.event,
                name='موعد غائب', phone='+963991234561',
                status=Reservation.Status.CONFIRMED, party_size=1,
            ))

    def test_ticket_tier_capacity_is_per_performance(self):
        tier = EventTicketType.objects.create(
            event=self.event, name_ar='قريب من المسرح', price_syp=100000, capacity=2)
        first = self.book(self.early, size=2)
        second = self.book(self.late, size=2)
        set_event_ticket_allocations(first.pk, {tier.pk: 2}, actor=self.staff)
        set_event_ticket_allocations(second.pk, {tier.pk: 2}, actor=self.staff)
        self.assertEqual(first.ticket_allocations.get().quantity, 2)
        self.assertEqual(second.ticket_allocations.get().quantity, 2)

    def test_cancelled_show_refuses_new_bookings_and_active_bookings_block_cancellation(self):
        self.book(self.early)
        with self.assertRaises(ValidationError):
            cancel_performance(self.early.pk, actor=self.staff)
        cancel_performance(self.late.pk, actor=self.staff)
        with self.assertRaises(ValidationError):
            self.book(self.late)

    def test_browser_may_request_each_show_but_not_reuse_key_for_different_show(self):
        common = dict(event_uuid=self.event.uuid, owner_hash='a' * 64, ip_hash='b' * 64,
                      name='ضيف', phone='+963991234567', party_size=1)
        first_key = uuid4()
        first = submit_request(**common, client_key=first_key, performance_uuid=self.early.uuid)
        self.assertEqual(submit_request(**common, client_key=first_key,
                                        performance_uuid=self.early.uuid).pk, first.pk)
        with self.assertRaises(ValidationError):
            submit_request(**common, client_key=first_key, performance_uuid=self.late.uuid)
        second = submit_request(**common, client_key=uuid4(), performance_uuid=self.late.uuid)
        self.assertNotEqual(first.pk, second.pk)
        review_request(first.uuid, actor=self.staff, action='approve')
        review_request(second.uuid, actor=self.staff, action='approve')
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.reservation.performance_id, self.early.pk)
        self.assertEqual(second.reservation.performance_id, self.late.pk)

    def test_public_page_displays_two_shows_under_one_event(self):
        response = self.client.get(reverse('public_event_detail', args=[self.event.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'مواعيد العروض')
        self.assertContains(response, str(self.early.uuid))
        self.assertContains(response, str(self.late.uuid))

    def test_import_requires_show_and_fingerprints_independent_batches(self):
        tier = EventTicketType.objects.create(
            event=self.event, name_ar='أساسية', price_syp=75000, capacity=2)
        rows = [{'name': 'ضيف', 'phone': '+963991234567', 'party_size': 1,
                 'status': 'confirmed', 'tiers': {str(tier.pk): 1}}]
        with self.assertRaises(ValidationError):
            validate_rows(self.event.pk, rows)
        canonical, first_fp = validate_rows(self.event.pk, rows, performance_id=self.early.pk)
        _, created = import_event_bookings(self.event.pk, canonical, first_fp,
                                           actor=self.staff, performance_id=self.early.pk)
        self.assertTrue(created)
        canonical, second_fp = validate_rows(self.event.pk, rows, performance_id=self.late.pk)
        self.assertNotEqual(first_fp, second_fp)
        _, created = import_event_bookings(self.event.pk, canonical, second_fp,
                                           actor=self.staff, performance_id=self.late.pk)
        self.assertTrue(created)
        self.assertEqual(Reservation.objects.filter(event=self.event).count(), 2)

    def test_staff_can_add_second_show_without_second_event(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('staff_event_schedule', args=[self.event.pk]), {
            'starts_at': timezone.localtime(self.event.starts_at, DAMASCUS).strftime('%Y-%m-%dT%H:%M'),
            'frequency': 'once', 'count': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(self.event.performances.count(), 2)
        response = self.client.get(reverse('staff_event_detail', args=[self.event.pk]))
        self.assertContains(response, 'مواعيد العروض')


class PerformanceRecurrenceTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='recurrence-admin', phone='+963991000200',
            password='staff', role='admin',
        )

    def test_weekly_generation_is_bounded_and_idempotent(self):
        start = timezone.make_aware(datetime(2026, 11, 17, 20), DAMASCUS)
        event = Event.objects.create(title_ar='عود الثلاثاء', starts_at=start)
        first = add_performances(event.pk, actor=self.staff, starts_at=start,
                                 frequency='weekly', count=4)
        second = add_performances(event.pk, actor=self.staff, starts_at=start,
                                  frequency='weekly', count=4)
        self.assertEqual([row.pk for row in first], [row.pk for row in second])
        self.assertEqual(event.performances.count(), 4)
        self.assertEqual(Event.objects.count(), 1)
        with self.assertRaises(ValidationError):
            add_performances(event.pk, actor=self.staff, starts_at=start,
                             frequency='weekly', count=13)

    def test_monthly_recurrence_anchors_to_original_day(self):
        start = timezone.make_aware(datetime(2027, 1, 31, 19), DAMASCUS)
        event = Event.objects.create(title_ar='لقاء شهري', starts_at=start)
        add_performances(event.pk, actor=self.staff, starts_at=start,
                         frequency='monthly', count=3)
        dates = [timezone.localtime(show.starts_at, DAMASCUS).date().isoformat()
                 for show in event.performances.all()]
        self.assertEqual(dates, ['2027-01-31', '2027-02-28', '2027-03-31'])
