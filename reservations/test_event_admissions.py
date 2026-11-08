from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import HubVisit
from events.models import Event
from reservations.admissions import set_event_admission_total
from reservations.models import EventAdmission, Reservation
from reservations.services import change_reservation_status, create_reservation


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventAdmissionTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='admission-admin', phone='+990802', role='admin', password='test',
        )
        self.event = Event.objects.create(
            title_ar='ليلة الحضور', starts_at=timezone.now(), capacity=20,
        )
        self.booking = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='ضيف الحضور',
            phone='+96370000001', party_size=3, status='confirmed',
        ))

    def test_ticket_only_arrival_is_idempotent_without_restaurant_visit(self):
        before_visits = HubVisit.objects.count()
        first, changed = set_event_admission_total(
            self.booking.pk, 1, actor=self.manager,
        )
        self.assertTrue(changed)
        self.assertEqual(first.arrived_count, 1)
        again, changed = set_event_admission_total(
            self.booking.pk, 1, actor=self.manager,
        )
        self.assertFalse(changed)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(HubVisit.objects.count(), before_visits)
        self.assertIsNone(Reservation.objects.get(pk=self.booking.pk).visit_id)
        updated, changed = set_event_admission_total(
            self.booking.pk, 3, actor=self.manager,
        )
        self.assertTrue(changed)
        self.assertEqual(updated.arrived_count, 3)

    def test_reject_decrease_oversized_and_missing_attendance(self):
        set_event_admission_total(self.booking.pk, 2, actor=self.manager)
        for amount in (1, 4, 0, -1, '1.5'):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                set_event_admission_total(self.booking.pk, amount, actor=self.manager)
        self.assertEqual(self.booking.event_admission.arrived_count, 2)

    def test_no_show_and_cancellation_blocked_after_arrival(self):
        set_event_admission_total(self.booking.pk, 1, actor=self.manager)
        for status in ('no_show', 'cancelled'):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                change_reservation_status(self.booking.pk, status, actor=self.manager)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'confirmed')

    def test_early_arrival_is_not_allowed(self):
        self.event.starts_at = timezone.now() + timedelta(days=2)
        self.event.save(update_fields=['starts_at'])
        with self.assertRaises(ValidationError):
            set_event_admission_total(self.booking.pk, 1, actor=self.manager)

    def test_staff_search_and_post_count(self):
        self.client.force_login(self.manager)
        url = reverse('staff_event_checkin', args=[self.event.pk])
        response = self.client.get(url, {'q': 'ضيف الحضور'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تسجيل وصول الجمهور')
        self.assertContains(response, 'ضيف الحضور')
        self.assertContains(response, 'ticket-only', count=0)
        self.assertEqual(self.client.post(url, {
            'reservation_id': self.booking.pk, 'arrived_count': 2,
        }).status_code, 302)
        self.assertEqual(
            EventAdmission.objects.get(reservation=self.booking).arrived_count, 2,
        )
        self.assertIsNone(Reservation.objects.get(pk=self.booking.pk).visit_id)
        self.assertEqual(
            self.client.get(reverse('staff_event_detail', args=[self.event.pk])).status_code, 200,
        )

    def test_anonymous_cannot_access_arrivals(self):
        self.assertEqual(
            self.client.get(reverse('staff_event_checkin', args=[self.event.pk])).status_code, 302,
        )


class ConcurrentEventAdmissionsTests(TransactionTestCase):
    def test_simultaneous_updates_cannot_overcount_same_total(self):
        manager = get_user_model().objects.create_user(
            username='admission-concurrent', phone='+990803', role='admin', password='test',
        )
        event = Event.objects.create(title_ar='مدخل', starts_at=timezone.now(), capacity=10)
        row = create_reservation(Reservation(
            reservation_type='event', event=event, name='ضيف',
            phone='+96370000099', party_size=4, status='confirmed',
        ))
        barrier = Barrier(2)

        def mark():
            close_old_connections()
            barrier.wait()
            try:
                return set_event_admission_total(row.pk, 2, actor=manager)[1]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: mark(), range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(
            EventAdmission.objects.get(reservation=row).arrived_count, 2,
        )
