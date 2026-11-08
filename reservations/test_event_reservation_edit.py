"""Event reservation edit tests for capacity, tiers and financial freeze."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Order, Payment
from events.models import Event, EventTicketType
from reservations.editing import edit_event_reservation
from reservations.models import (
    EventAdmission, Reservation, ReservationTicketAllocation,
    ReservationTicketPaymentLink,
)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventReservationEditTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='res-edit-admin', phone='988773', password='test', role='admin',
        )
        self.event = Event.objects.create(
            title_ar='حفلة تعديل', starts_at=timezone.now() + timedelta(days=5),
            status=Event.Status.PUBLISHED, capacity=4,
        )
        self.front = EventTicketType.objects.create(
            event=self.event, name_ar='أمام', price_syp=1000, capacity=2,
        )
        self.rear = EventTicketType.objects.create(
            event=self.event, name_ar='خلف', price_syp=750, capacity=2,
        )
        self.booking = Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT, event=self.event,
            name='ضيف', phone='+963911111111', party_size=2,
            status=Reservation.Status.CONFIRMED,
        )
        ReservationTicketAllocation.objects.create(
            reservation=self.booking, ticket_type=self.front,
            quantity=1, unit_price_syp=1000,
        )
        ReservationTicketAllocation.objects.create(
            reservation=self.booking, ticket_type=self.rear,
            quantity=1, unit_price_syp=750,
        )

    def edit(self, size, tiers, *, name='ضيف محرر'):
        return edit_event_reservation(
            self.booking.pk, actor=self.manager, name=name,
            phone='+963911111111', party_size=size, notes='ملاحظة',
            tier_quantities=tiers,
        )

    def test_changes_size_and_tiers_atomically(self):
        self.edit(3, {self.front.pk: 2, self.rear.pk: 1})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.party_size, 3)
        self.assertEqual(self.booking.name, 'ضيف محرر')
        self.assertEqual(
            list(self.booking.ticket_allocations.order_by('ticket_type_id').values_list('quantity', flat=True)),
            [2, 1],
        )

    def test_oversold_overall_capacity_rolls_back_edits(self):
        Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT, event=self.event,
            name='ضيف آخر', phone='+963922222222', party_size=2,
            status=Reservation.Status.CONFIRMED,
        )
        with self.assertRaises(ValidationError):
            self.edit(3, {self.front.pk: 2, self.rear.pk: 1})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.party_size, 2)
        self.assertEqual(self.booking.name, 'ضيف')

    def test_tier_limit_failure_rolls_back_name_and_size(self):
        with self.assertRaises(ValidationError):
            self.edit(3, {self.front.pk: 3})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.party_size, 2)
        self.assertEqual(self.booking.name, 'ضيف')

    def test_payment_link_freezes_counts_but_allows_contact_correction(self):
        order = Order.objects.create()
        payment = Payment.objects.create(order=order, amount_syp=1, method=Payment.Method.CASH)
        ReservationTicketPaymentLink.objects.create(
            reservation=self.booking, payment=payment, linked_by=self.manager,
        )
        with self.assertRaises(ValidationError):
            self.edit(3, {self.front.pk: 2, self.rear.pk: 1})
        self.edit(2, {self.front.pk: 1, self.rear.pk: 1}, name='اسم مصحح')
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.name, 'اسم مصحح')

    def test_checkin_blocks_edits(self):
        EventAdmission.objects.create(
            reservation=self.booking, arrived_count=1, recorded_by=self.manager,
        )
        with self.assertRaises(ValidationError):
            self.edit(2, {self.front.pk: 1, self.rear.pk: 1})

    def test_staff_route_requires_login_and_exposes_arabic_form(self):
        url = reverse('staff_event_reservation_edit', args=[self.booking.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.manager)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'تعديل الحجز')
        self.assertContains(page, 'فئات التذاكر')
