"""Event booking CSV imports: privacy, idempotency and transaction tests."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventTicketType
from reservations.importing import (
    import_event_bookings, preview_uploaded_csv, validate_rows,
)
from reservations.models import Reservation, ReservationImportBatch, ReservationTicketAllocation


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventBookingImportTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='booking-import-manager', phone='987790', password='test', role='admin',
        )
        self.event = Event.objects.create(
            title_ar='ليلة استيراد',
            starts_at=timezone.now() + timedelta(days=8),
            status=Event.Status.PUBLISHED, capacity=7,
        )
        self.front = EventTicketType.objects.create(
            event=self.event, name_ar='قريب من المسرح', price_syp=1000, capacity=4,
        )
        self.rear = EventTicketType.objects.create(
            event=self.event, name_ar='خلفي', price_syp=750, capacity=3,
        )
        self.url = reverse('staff_event_booking_import', args=[self.event.pk])

    def csv(self, rows=None):
        content = rows or (
            'name,phone,party_size,status,ticket_%s,ticket_%s\n'
            'ضيف أول,+963911111111,2,confirmed,1,1\n'
            'ضيف ثان,+963922222222,1,pending,0,1\n'
        ) % (self.front.pk, self.rear.pk)
        return SimpleUploadedFile('bookings.csv', content.encode('utf-8'), content_type='text/csv')

    def test_preview_does_not_create_bookings(self):
        rows, digest = preview_uploaded_csv(self.event.pk, self.csv())
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(digest), 64)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(ReservationImportBatch.objects.count(), 0)

    def test_confirm_uses_same_digest_and_is_idempotent(self):
        rows, digest = preview_uploaded_csv(self.event.pk, self.csv())
        result, first = import_event_bookings(self.event.pk, rows, digest, actor=self.manager)
        self.assertTrue(first)
        self.assertEqual(result.imported_count, 2)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(ReservationTicketAllocation.objects.count(), 3)
        again, second = import_event_bookings(self.event.pk, rows, digest, actor=self.manager)
        self.assertFalse(second)
        self.assertEqual(again.pk, result.pk)
        self.assertEqual(Reservation.objects.count(), 2)

    def test_wrong_fingerprint_rejected_without_writes(self):
        rows, digest = preview_uploaded_csv(self.event.pk, self.csv())
        with self.assertRaises(ValidationError):
            import_event_bookings(self.event.pk, rows, '0'*64, actor=self.manager)
        self.assertFalse(Reservation.objects.exists())

    def test_capacity_failure_rolls_back_everything(self):
        Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=self.event, name='سبق', phone='+963933333333',
            party_size=5, status=Reservation.Status.CONFIRMED,
        )
        rows, digest = preview_uploaded_csv(self.event.pk, self.csv())
        with self.assertRaises(ValidationError):
            import_event_bookings(self.event.pk, rows, digest, actor=self.manager)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertFalse(ReservationImportBatch.objects.exists())

    def test_existing_contact_different_format_is_duplicate(self):
        Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=self.event, name='مكرر', phone='+963 911-111111',
            party_size=1, status=Reservation.Status.PENDING,
        )
        rows, digest = preview_uploaded_csv(self.event.pk, self.csv())
        with self.assertRaises(ValidationError):
            import_event_bookings(self.event.pk, rows, digest, actor=self.manager)
        self.assertFalse(ReservationImportBatch.objects.exists())

    def test_existing_phone_without_plus_cannot_be_imported_again(self):
        Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=self.event, name='حجز سابق', phone='963911111111',
            party_size=1, status=Reservation.Status.CONFIRMED,
        )
        rows, digest = preview_uploaded_csv(self.event.pk, self.csv())
        with self.assertRaises(ValidationError):
            import_event_bookings(self.event.pk, rows, digest, actor=self.manager)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertFalse(ReservationImportBatch.objects.exists())

    def test_incoming_plus_and_plain_country_code_are_same_contact(self):
        with self.assertRaises(ValidationError):
            preview_uploaded_csv(self.event.pk, self.csv(
                f'name,phone,party_size,status,ticket_{self.front.pk}\n'
                'ضيف أول,+963911111111,1,confirmed,1\n'
                'ضيف ثان,963911111111,1,pending,1\n'
            ))
        self.assertFalse(Reservation.objects.exists())

    def test_form_requires_login_and_preview_does_not_save(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.post(self.url, {
            'action': 'preview', 'bookings_csv': self.csv(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ضيف أول')
        self.assertContains(response, 'معاينة 2 حجزاً')
        self.assertFalse(Reservation.objects.exists())

    def test_form_confirm_imports_and_template_has_only_headers(self):
        self.client.force_login(self.manager)
        preview = self.client.post(self.url, {'action': 'preview', 'bookings_csv': self.csv()})
        digest = preview.context['fingerprint']
        done = self.client.post(self.url, {
            'action': 'confirm', 'fingerprint': digest, 'bookings_csv': self.csv(),
        })
        self.assertEqual(done.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 2)
        csv_response = self.client.get(self.url + '?template=1')
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(f'ticket_{self.front.pk}', csv_response.content.decode('utf-8'))
        self.assertNotIn('ضيف أول', csv_response.content.decode('utf-8'))

    def test_duplicate_incoming_phone_and_unsafe_name_rejected(self):
        with self.assertRaises(ValidationError):
            preview_uploaded_csv(self.event.pk, self.csv(
                f'name,phone,party_size,status,ticket_{self.front.pk}\n'
                '=HYPERLINK,+963911111111,1,confirmed,1\n'
            ))
        with self.assertRaises(ValidationError):
            preview_uploaded_csv(self.event.pk, self.csv(
                f'name,phone,party_size,status,ticket_{self.front.pk}\n'
                'ضيف,+963911111111,1,confirmed,1\n'
                'ضيف آخر,+963911111111,1,pending,1\n'
            ))
