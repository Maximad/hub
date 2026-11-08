"""Ticket tier allocation contract and regressions (PostgreSQL CI)."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventTicketType
from reservations.models import Reservation, ReservationTicketAllocation
from reservations.services import change_reservation_status, create_reservation
from reservations.ticketing import set_event_ticket_allocations


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class TicketAllocationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username='ticket-alloc-manager', phone='+990735', password='test', role='admin',
        )
        self.event = Event.objects.create(
            title_ar='تجربة حجز', starts_at=timezone.now(), capacity=5,
        )
        self.front = EventTicketType.objects.create(
            event=self.event, name_ar='أمام المسرح', price_syp=1000, capacity=2,
        )
        self.back = EventTicketType.objects.create(
            event=self.event, name_ar='خلف المسرح', price_syp=750, capacity=3,
        )
        self.one = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='الحجز الأول',
            phone='+96300000001', party_size=3, status='confirmed',
        ))

    def test_group_tiers_match_party_and_snapshot_price(self):
        set_event_ticket_allocations(
            self.one.pk, {self.front.pk: 2, self.back.pk: 1}, actor=self.manager,
        )
        rows = {row.ticket_type_id: row for row in self.one.ticket_allocations.all()}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[self.front.pk].unit_price_syp, 1000)
        self.assertEqual(rows[self.back.pk].unit_price_syp, 750)
        self.front.price_syp = 1200
        self.front.save(update_fields=['price_syp'])
        set_event_ticket_allocations(
            self.one.pk, {self.front.pk: 1, self.back.pk: 2}, actor=self.manager,
        )
        self.assertEqual(
            ReservationTicketAllocation.objects.get(
                reservation=self.one, ticket_type=self.front,
            ).unit_price_syp, 1000,
        )

    def test_mismatched_total_rejected_atomically(self):
        with self.assertRaises(ValidationError):
            set_event_ticket_allocations(self.one.pk, {self.front.pk: 1}, actor=self.manager)
        self.assertEqual(self.one.ticket_allocations.count(), 0)

    def test_other_event_and_negative_quantity_rejected(self):
        other_event = Event.objects.create(title_ar='غير مرتبطة', starts_at=timezone.now())
        foreign = EventTicketType.objects.create(event=other_event, name_ar='خارجية', price_syp=200)
        for quantities in ({foreign.pk: 3}, {self.front.pk: -1}, {self.front.pk: '1.5'}):
            with self.subTest(quantities=quantities), self.assertRaises(ValidationError):
                set_event_ticket_allocations(self.one.pk, quantities, actor=self.manager)
        self.assertEqual(self.one.ticket_allocations.count(), 0)

    def test_tier_cap_counts_pending_and_confirmed_and_frees_on_cancellation(self):
        set_event_ticket_allocations(
            self.one.pk, {self.front.pk: 2, self.back.pk: 1}, actor=self.manager,
        )
        second = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='الحجز الثاني',
            phone='+96300000002', party_size=2, status='pending',
        ))
        with self.assertRaises(ValidationError):
            set_event_ticket_allocations(
                second.pk, {self.front.pk: 1, self.back.pk: 1}, actor=self.manager,
            )
        self.assertEqual(second.ticket_allocations.count(), 0)
        change_reservation_status(self.one.pk, 'cancelled', actor=self.manager)
        set_event_ticket_allocations(
            second.pk, {self.front.pk: 1, self.back.pk: 1}, actor=self.manager,
        )
        self.assertEqual(second.ticket_allocations.count(), 2)

    def test_cannot_allocate_to_cancelled_reservation(self):
        change_reservation_status(self.one.pk, 'cancelled', actor=self.manager)
        with self.assertRaises(ValidationError):
            set_event_ticket_allocations(
                self.one.pk, {self.front.pk: 2, self.back.pk: 1}, actor=self.manager,
            )

    def test_status_confirmation_rechecks_existing_allocation(self):
        pending = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='مؤجل',
            phone='+96300000003', party_size=2, status='pending',
        ))
        set_event_ticket_allocations(pending.pk, {self.back.pk: 2}, actor=self.manager)
        self.back.capacity = 1
        self.back.save(update_fields=['capacity'])
        with self.assertRaises(ValidationError):
            change_reservation_status(pending.pk, 'confirmed', actor=self.manager)
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'pending')

    def test_web_editor_and_arabic_status(self):
        self.client.force_login(self.manager)
        url = reverse('staff_reservation_tickets', args=[self.one.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'توزيع فئات التذاكر')
        self.assertContains(response, 'أمام المسرح')
        self.assertEqual(
            self.client.post(url, {
                f'tier_{self.front.pk}': '2',
                f'tier_{self.back.pk}': '1',
            }).status_code,
            302,
        )
        self.assertEqual(self.one.ticket_allocations.count(), 2)
        booking = self.client.get(reverse('staff_reservation_detail', args=[self.one.pk]))
        self.assertContains(booking, 'تحديد أو مراجعة فئات التذاكر')
        self.assertContains(booking, 'لا يثبت الدفع')
        self.assertEqual(dict(Reservation.Status.choices)['pending'], 'بانتظار التأكيد')

    def test_anonymous_cannot_edit(self):
        self.assertEqual(
            self.client.get(reverse('staff_reservation_tickets', args=[self.one.pk])).status_code,
            302,
        )


class ConcurrentTierAllocationTests(TransactionTestCase):
    reset_sequences = True

    def test_event_lock_prevents_two_staff_allocations_overselling_one_tier(self):
        User = get_user_model()
        manager = User.objects.create_user(
            username='alloc-concurrency', phone='+990736', password='test', role='admin',
        )
        event = Event.objects.create(
            title_ar='سعة صغيرة', starts_at=timezone.now(), capacity=4,
        )
        tier = EventTicketType.objects.create(
            event=event, name_ar='محدودة', price_syp=1000, capacity=2,
        )
        reservations = [
            create_reservation(Reservation(
                reservation_type='event', event=event, name=f'ضيف {i}',
                phone=f'+96350{i}', party_size=2, status='confirmed',
            ))
            for i in range(2)
        ]
        barrier = Barrier(2)

        def allocate(reservation_id):
            close_old_connections()
            barrier.wait()
            try:
                set_event_ticket_allocations(
                    reservation_id, {tier.pk: 2}, actor=manager,
                )
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(allocate, [row.pk for row in reservations]))
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(
            ReservationTicketAllocation.objects.filter(ticket_type=tier).count(), 1,
        )
