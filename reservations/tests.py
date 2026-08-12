from datetime import datetime, time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from core.models import Room, TableArea
from events.models import Event
from .models import Reservation
from .services import change_reservation_status, create_reservation


class ReservationWorkflowTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='reservation-admin', password='secret', is_superuser=True,
        )
        self.room = Room.objects.create(name_ar='الاستوديو', name_en='Studio')
        self.other_room = Room.objects.create(name_ar='سفرة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 1')
        self.event = Event.objects.create(title_ar='أمسية', title_en='Evening', starts_at=timezone.make_aware(datetime(2026, 7, 30, 20)), room=self.room)

    def test_event_reservation_uses_effective_event_schedule(self):
        reservation = Reservation(reservation_type=Reservation.ReservationType.EVENT, event=self.event, name='ضيف', phone='0930000000')
        reservation.full_clean()
        self.assertEqual(reservation.effective_date, self.event.starts_at.date())
        self.assertEqual(reservation.effective_starts_at, self.event.starts_at.time())
        self.assertEqual(reservation.effective_room, self.room)

    def test_event_reservation_requires_event(self):
        with self.assertRaises(ValidationError):
            Reservation(reservation_type='event', name='ضيف', phone='0930000000').full_clean()

    def test_regular_reservation_validates_schedule_and_room_table(self):
        reservation = Reservation(reservation_type='regular', name='ضيف', phone='0930000000', reservation_date=self.event.starts_at.date(), start_time=time(10), end_time=time(9), room=self.other_room, table_area=self.table)
        with self.assertRaises(ValidationError) as error:
            reservation.full_clean()
        self.assertIn('end_time', error.exception.message_dict)
        self.assertIn('table_area', error.exception.message_dict)

    def test_regular_reservation_requires_date_and_start(self):
        with self.assertRaises(ValidationError) as error:
            Reservation(reservation_type='regular', name='ضيف', phone='0930000000').full_clean()
        self.assertIn('reservation_date', error.exception.message_dict)
        self.assertIn('start_time', error.exception.message_dict)

    def test_table_endpoint_is_authorized_limited_and_room_filtered(self):
        user = get_user_model().objects.create_user(username='staff', password='secret', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        response = self.client.get(reverse('staff_reservation_tables'), {'room': self.room.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['id'], self.table.pk)
        self.assertNotIn('qr_token', response.content.decode())

    def test_table_endpoint_rejects_anonymous(self):
        response = self.client.get(reverse('staff_reservation_tables'), {'room': self.room.pk})
        self.assertEqual(response.status_code, 302)

    def regular(self, **overrides):
        values = dict(reservation_type='regular', name='ضيف', phone='0930000000',
                      reservation_date=self.event.starts_at.date(), start_time=time(10),
                      end_time=time(11), room=self.room, status='pending')
        values.update(overrides)
        return create_reservation(Reservation(**values))

    def test_boundary_touching_intervals_do_not_overlap(self):
        self.regular(table_area=self.table)
        second = self.regular(start_time=time(11), end_time=time(12), table_area=self.table)
        self.assertIsNotNone(second.pk)

    def test_cancelled_and_no_show_do_not_block(self):
        for index, status in enumerate(('cancelled', 'no_show')):
            table = TableArea.objects.create(room=self.room, name_ar=f'طاولة {index + 2}')
            self.regular(table_area=table, status=status)
            self.assertIsNotNone(self.regular(table_area=table).pk)

    def test_missing_end_time_gets_configured_default(self):
        row = self.regular(start_time=time(10), end_time=None)
        self.assertEqual(row.end_time, time(12))

    def test_room_only_booking_conflicts_with_table_booking(self):
        self.regular(table_area=self.table)
        with self.assertRaises(ValidationError):
            self.regular(table_area=None)

    def test_same_table_conflicts_but_other_table_does_not(self):
        other_table = TableArea.objects.create(room=self.room, name_ar='طاولة أخرى')
        self.regular(table_area=self.table)
        with self.assertRaises(ValidationError):
            self.regular(table_area=self.table)
        self.assertIsNotNone(self.regular(table_area=other_table).pk)

    def test_confirmed_event_cannot_exceed_active_attendance(self):
        self.event.capacity = 5
        self.event.save(update_fields=['capacity'])
        create_reservation(Reservation(reservation_type='event', event=self.event, name='أ', phone='1', party_size=3, status='pending'))
        candidate = Reservation.objects.create(reservation_type='event', event=self.event, name='ب', phone='2', party_size=3, status='cancelled')
        with self.assertRaises(ValidationError):
            change_reservation_status(candidate.pk, 'confirmed', actor=self.admin,
                                      correction=True, reason='restore booking')
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, 'cancelled')

    def test_cancelled_event_attendance_does_not_consume_capacity(self):
        self.event.capacity = 2
        self.event.save(update_fields=['capacity'])
        create_reservation(Reservation(reservation_type='event', event=self.event, name='أ', phone='1', party_size=2, status='cancelled'))
        row = create_reservation(Reservation(reservation_type='event', event=self.event, name='ب', phone='2', party_size=2, status='confirmed'))
        self.assertIsNotNone(row.pk)


class ConcurrentEventConfirmationTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_confirmations_cannot_both_exceed_capacity(self):
        room = Room.objects.create(name_ar='قاعة')
        event = Event.objects.create(title_ar='فعالية', starts_at=timezone.now(), room=room, capacity=5)
        candidates = [
            Reservation.objects.create(reservation_type='event', event=event, name=str(i), phone=str(i), party_size=3, status='cancelled')
            for i in range(2)
        ]
        admin = get_user_model().objects.create_user(
            username='capacity-admin', password='secret', is_superuser=True,
        )
        barrier = Barrier(2)

        def confirm(pk):
            close_old_connections()
            barrier.wait()
            try:
                change_reservation_status(pk, 'confirmed', actor=admin,
                                          correction=True, reason='restore booking')
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, [row.pk for row in candidates]))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(Reservation.objects.filter(event=event, status='confirmed').count(), 1)
