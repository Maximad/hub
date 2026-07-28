from datetime import datetime, time
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from core.models import Room, TableArea
from events.models import Event
from .models import Reservation


class ReservationWorkflowTests(TestCase):
    def setUp(self):
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
