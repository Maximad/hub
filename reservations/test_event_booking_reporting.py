"""Read-only event reporting tests; do not infer payment from ticket snapshots."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventTicketType
from reservations.models import (
    EventAdmission, Reservation, ReservationPaymentArrangement,
    ReservationTicketAllocation,
)
from reservations.reporting import event_booking_report


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventBookingReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_user(
            username='report-admin', phone='989911', password='test', role='admin',
        )
        cls.event = Event.objects.create(
            title_ar='ليلة التقرير',
            starts_at=timezone.now() + timedelta(days=7),
            status=Event.Status.PUBLISHED,
            capacity=20,
        )
        cls.front = EventTicketType.objects.create(
            event=cls.event, name_ar='قريب من المسرح', price_syp=1000, capacity=10,
        )
        cls.rear = EventTicketType.objects.create(
            event=cls.event, name_ar='بعيد عن المسرح', price_syp=750, capacity=10,
        )

    def booking(self, *, name, size, status):
        return Reservation.objects.create(
            name=name, phone='+963900000555',
            event=self.event, party_size=size,
            reservation_type=Reservation.ReservationType.EVENT,
            status=status,
        )

    def test_empty_event_report_uses_zeroes_not_revenue(self):
        report = event_booking_report(self.event.id)
        self.assertEqual(report['confirmed_people'], 0)
        self.assertEqual(report['booking_face_value_syp'], 0)
        self.assertEqual(report['unclassified_people'], 0)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('staff_event_detail', args=[self.event.id]))
        self.assertContains(response, 'متابعة الحضور والحجوزات')
        self.assertContains(response, 'ليس إيراداً محصّلاً')

    def test_report_excludes_cancelled_allocation_from_face_value(self):
        confirmed = self.booking(name='مؤكد', size=3, status=Reservation.Status.CONFIRMED)
        pending = self.booking(name='قيد الانتظار', size=2, status=Reservation.Status.PENDING)
        cancelled = self.booking(name='ملغى', size=2, status=Reservation.Status.CANCELLED)
        ReservationTicketAllocation.objects.create(
            reservation=confirmed, ticket_type=self.front, quantity=2, unit_price_syp=1000,
        )
        ReservationTicketAllocation.objects.create(
            reservation=confirmed, ticket_type=self.rear, quantity=1, unit_price_syp=750,
        )
        ReservationTicketAllocation.objects.create(
            reservation=cancelled, ticket_type=self.front, quantity=2, unit_price_syp=1000,
        )
        ReservationPaymentArrangement.objects.create(
            reservation=confirmed,
            mode=ReservationPaymentArrangement.Mode.STAFF_GUARANTEE,
            guaranteed_by=self.manager,
        )
        EventAdmission.objects.create(
            reservation=confirmed, arrived_count=2, recorded_by=self.manager,
        )
        report = event_booking_report(self.event.id)
        self.assertEqual(report['confirmed_people'], 3)
        self.assertEqual(report['pending_people'], 2)
        self.assertEqual(report['cancelled_people'], 2)
        self.assertEqual(report['admitted_people'], 2)
        self.assertEqual(report['guaranteed_people'], 3)
        self.assertEqual(report['classified_people'], 3)
        self.assertEqual(report['unclassified_people'], 2)
        self.assertEqual(report['booking_face_value_syp'], 2750)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('staff_event_detail', args=[self.event.id]))
        self.assertContains(response, 'قريب من المسرح')
        self.assertContains(response, '2750')
        self.assertContains(response, 'حجوزات نشطة بلا فئة تذكرة')

    def test_event_page_requires_login(self):
        response = self.client.get(reverse('staff_event_detail', args=[self.event.id]))
        self.assertEqual(response.status_code, 302)
