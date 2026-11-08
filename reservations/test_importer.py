"""Private CSV onboarding: preview rollback, idempotent apply, no contact leaks."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventTicketType
from reservations.importer import BookingImportError, import_booking_csv
from reservations.models import Reservation, ReservationImportRecord


class EventBookingImportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='booking-importer', phone='+990914', role='admin', password='test',
        )
        self.event = Event.objects.create(
            title_ar='فعالية تجريبية', starts_at=timezone.now(), capacity=6,
        )
        self.front = EventTicketType.objects.create(
            event=self.event, name_ar='قريبة', price_syp=1000, capacity=3,
        )
        self.back = EventTicketType.objects.create(
            event=self.event, name_ar='خلفية', price_syp=750, capacity=3,
        )

    def csv(self, *, reference='BK001', name='اسم خاص', phone='+96390012345',
            quantity=3, status='confirmed', tickets=None):
        if tickets is None:
            tickets = f'{self.front.pk}:2;{self.back.pk}:1'
        return (
            'event_uuid,reference,name,phone,party_size,status,tickets\n'
            f'{self.event.uuid},{reference},{name},{phone},{quantity},{status},{tickets}\n'
        )

    def test_preview_rolls_back_and_apply_is_idempotent(self):
        source = 'whatsapp-20261121'
        text = self.csv()
        preview = import_booking_csv(text, source=source, actor=self.admin)
        self.assertEqual(preview['created'], 1)
        self.assertFalse(preview['applied'])
        self.assertEqual(ReservationImportRecord.objects.count(), 0)
        self.assertEqual(Reservation.objects.count(), 0)
        done = import_booking_csv(text, source=source, actor=self.admin, apply=True)
        self.assertEqual(done['created'], 1)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationImportRecord.objects.count(), 1)
        again = import_booking_csv(text, source=source, actor=self.admin, apply=True)
        self.assertEqual(again['skipped'], 1)
        self.assertEqual(Reservation.objects.count(), 1)
        reservation = Reservation.objects.get()
        self.assertEqual(
            sum(reservation.ticket_allocations.values_list('quantity', flat=True)),
            reservation.party_size,
        )

    def test_conflicting_reference_rejected_without_personal_data_in_errors(self):
        original = self.csv()
        import_booking_csv(original, source='manual', actor=self.admin, apply=True)
        with self.assertRaises(BookingImportError) as error:
            import_booking_csv(
                self.csv(name='different private name'),
                source='manual', actor=self.admin, apply=True,
            )
        self.assertEqual(error.exception.code, 'imported_reference_conflict')
        self.assertNotIn('different private name', str(error.exception))
        self.assertEqual(Reservation.objects.count(), 1)

    def test_duplicate_key_and_overcapacity_rollback_entire_file(self):
        first = self.csv()
        second = self.csv(reference='BK002', phone='+96390022222', quantity=4,
                          tickets=f'{self.front.pk}:2;{self.back.pk}:2').splitlines()[1]
        full = first + second + '\n'
        with self.assertRaises(BookingImportError):
            import_booking_csv(full, source='manual', actor=self.admin, apply=True)
        self.assertEqual(Reservation.objects.count(), 0)
        with self.assertRaises(BookingImportError) as error:
            import_booking_csv(first + first.splitlines()[1]+'\n',
                               source='manual', actor=self.admin, apply=True)
        self.assertEqual(error.exception.code, 'duplicate_reference_in_file')

    def test_manual_duplicate_is_not_silently_imported(self):
        Reservation.objects.create(
            reservation_type='event', event=self.event, name='اسم خاص',
            phone='+96390012345', party_size=3, status='pending',
        )
        with self.assertRaises(BookingImportError) as error:
            import_booking_csv(self.csv(), source='manual', actor=self.admin, apply=True)
        self.assertEqual(error.exception.code, 'possible_existing_reservation')
        self.assertEqual(Reservation.objects.count(), 1)

    def test_command_preview_and_confirmed_row_count(self):
        text = self.csv()
        with TemporaryDirectory() as directory:
            path = Path(directory, 'bookings.csv')
            path.write_text(text, encoding='utf-8')
            output = StringIO()
            call_command(
                'import_event_bookings', file=str(path), source='manual',
                actor=self.admin.username, stdout=output,
            )
            self.assertIn('PREVIEW / ROLLED BACK', output.getvalue())
            self.assertNotIn('اسم خاص', output.getvalue())
            self.assertNotIn('+96390012345', output.getvalue())
            self.assertEqual(Reservation.objects.count(), 0)
            with self.assertRaises(CommandError):
                call_command(
                    'import_event_bookings', file=str(path), source='manual',
                    actor=self.admin.username, apply=True, expected_rows=2,
                )
            self.assertEqual(Reservation.objects.count(), 0)
            call_command(
                'import_event_bookings', file=str(path), source='manual',
                actor=self.admin.username, apply=True, expected_rows=1,
            )
            self.assertEqual(Reservation.objects.count(), 1)
