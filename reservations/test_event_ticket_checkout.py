"""Event ticket POS checkout contracts, PostgreSQL financial safety regressions."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import FinancialAccount, Payment, PostingBatch, Product, Order
from core.services.posting.exceptions import InvalidTransition
from events.models import Event, EventTicketType
from reservations.checkout import issue_ticket_order, collect_ticket_payment
from reservations.editing import edit_event_reservation
from reservations.finance_links import ticket_payment_summary, link_posted_ticket_payment
from reservations.models import (
    Reservation, ReservationTicketAllocation, ReservationTicketOrder,
    ReservationTicketPaymentLink,
)
from reservations.ticketing import set_event_ticket_allocations


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventTicketCheckoutTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='event-pos-admin', phone='989381', password='test', role='admin',
        )
        self.event = Event.objects.create(
            title_ar='حفلة محاسبية',
            status=Event.Status.PUBLISHED,
            starts_at=timezone.now()+timedelta(days=5),
            capacity=16,
        )
        self.front = EventTicketType.objects.create(
            event=self.event, name_ar='قريب', price_syp=1000, capacity=8,
        )
        self.rear = EventTicketType.objects.create(
            event=self.event, name_ar='بعيد', price_syp=750, capacity=8,
        )
        self.booking = Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=self.event, status=Reservation.Status.CONFIRMED,
            party_size=3, name='مشتري', phone='+963911111111',
        )
        ReservationTicketAllocation.objects.create(
            reservation=self.booking, ticket_type=self.front,
            quantity=2, unit_price_syp=1000,
        )
        ReservationTicketAllocation.objects.create(
            reservation=self.booking, ticket_type=self.rear,
            quantity=1, unit_price_syp=750,
        )

    def finance_accounts(self):
        FinancialAccount.objects.create(
            code='eventposrevenue', name_ar='إيراد تذاكر',
            account_type=FinancialAccount.AccountType.REVENUE, is_active=True,
        )
        FinancialAccount.objects.create(
            code='eventposcash', name_ar='صندوق',
            scope='cashbox', account_type=FinancialAccount.AccountType.ASSET,
            is_active=True,
        )

    def test_issuing_order_is_idempotent_and_uses_price_snapshots(self):
        self.front.price_syp = 1234
        self.front.save(update_fields=['price_syp'])
        sale, created = issue_ticket_order(self.booking.pk, actor=self.admin)
        self.assertTrue(created)
        self.assertEqual(sale.face_total_syp, 2750)
        self.assertEqual(sale.order.total_syp, 2750)
        self.assertEqual(sale.order.status, Order.Status.SERVED)
        self.assertFalse(sale.order.payments.exists())
        self.assertEqual(
            list(sale.order.items.order_by('unit_price_syp_snapshot').values_list('unit_price_syp_snapshot', flat=True)),
            [750, 1000],
        )
        for item in sale.order.items.select_related('product'):
            self.assertEqual(item.product.item_type, Product.ItemType.EVENT_TICKET)
            self.assertEqual(item.product.product_type, Product.ProductType.EVENT)
            self.assertFalse(item.product.visible_on_qr)
            self.assertFalse(item.product.requires_preparation)
        again, new = issue_ticket_order(self.booking.pk, actor=self.admin)
        self.assertFalse(new)
        self.assertEqual(sale.pk, again.pk)
        self.assertEqual(ReservationTicketOrder.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)

    def test_no_invoice_before_group_allocation_or_for_unconfirmed_booking(self):
        self.booking.status = Reservation.Status.PENDING
        self.booking.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            issue_ticket_order(self.booking.pk, actor=self.admin)
        self.assertEqual(Order.objects.count(), 0)

    def test_changing_tickets_after_issuing_invoice_is_blocked(self):
        issue_ticket_order(self.booking.pk, actor=self.admin)
        with self.assertRaises(ValidationError):
            set_event_ticket_allocations(
                self.booking.pk, {self.front.pk: 1, self.rear.pk: 2}, actor=self.admin,
            )
        with self.assertRaises(ValidationError):
            edit_event_reservation(
                self.booking.pk, actor=self.admin,
                name='بقي', phone='+963911111111', party_size=2,
                tier_quantities={self.front.pk: 1, self.rear.pk: 1},
            )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.party_size, 3)

    def test_cash_receipts_are_posted_linked_and_idempotent(self):
        self.finance_accounts()
        sale, _ = issue_ticket_order(self.booking.pk, actor=self.admin)
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 0)
        first = collect_ticket_payment(
            self.booking.pk, actor=self.admin, amount_syp=1000,
            method=Payment.Method.CASH, request_key='attempt-one-1111',
        )
        self.assertEqual(first.amount_syp, 1000)
        self.assertEqual(first.order_id, sale.order_id)
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'مدفوع جزئياً')
        self.assertEqual(ticket_payment_summary(self.booking)['outstanding'], 1750)
        repeated = collect_ticket_payment(
            self.booking.pk, actor=self.admin, amount_syp=1000,
            method=Payment.Method.CASH, request_key='attempt-one-1111',
        )
        self.assertEqual(repeated.pk, first.pk)
        self.assertEqual(Payment.objects.count(), 1)
        final = collect_ticket_payment(
            self.booking.pk, actor=self.admin, amount_syp=1750,
            method=Payment.Method.CASH, request_key='attempt-two-2222',
        )
        self.assertNotEqual(first.pk, final.pk)
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 2750)
        self.assertEqual(ticket_payment_summary(self.booking)['outstanding'], 0)
        self.assertEqual(ReservationTicketPaymentLink.objects.count(), 2)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.collect').count(), 2)
        with self.assertRaises(InvalidTransition):
            collect_ticket_payment(
                self.booking.pk, actor=self.admin, amount_syp=1,
                method=Payment.Method.CASH, request_key='attempt-three-3333',
            )
        self.assertEqual(Payment.objects.count(), 2)

    def test_foreign_payment_cannot_link_to_issued_booking(self):
        self.finance_accounts()
        sale, _ = issue_ticket_order(self.booking.pk, actor=self.admin)
        other = Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=self.event, status=Reservation.Status.CONFIRMED,
            party_size=1, name='ضيف ثان', phone='+963922222222',
        )
        ReservationTicketAllocation.objects.create(
            reservation=other, ticket_type=self.rear, quantity=1, unit_price_syp=750,
        )
        issue_ticket_order(other.pk, actor=self.admin)
        receipt = collect_ticket_payment(
            other.pk, actor=self.admin, amount_syp=750,
            method=Payment.Method.CASH, request_key='other-paid-555',
        )
        with self.assertRaises(ValidationError):
            link_posted_ticket_payment(self.booking.pk, receipt.pk, actor=self.admin)
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 0)
        self.assertEqual(sale.order.payments.count(), 0)

    def test_staff_checkout_screen_has_no_payment_before_invoice(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse('staff_reservation_ticket_checkout', args=[self.booking.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'إصدار فاتورة التذاكر دون قبض')
        self.assertContains(page, '2750')
        self.assertEqual(Payment.objects.count(), 0)
