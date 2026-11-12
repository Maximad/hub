"""Financial truth for event ticket receipts: only already posted POS payments."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Category, FinancialAccount, Order, OrderItem, Payment, PostingBatch, Product,
)
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect
from events.models import Event, EventTicketType
from reservations.checkout import issue_ticket_order
from reservations.finance_links import link_posted_ticket_payment, ticket_payment_summary
from reservations.models import Reservation, ReservationTicketPaymentLink
from reservations.services import create_reservation
from reservations.ticketing import set_event_ticket_allocations


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False, COLLECTION_ACCOUNT_CODES={'cash': 'cash:ticket-finance', 'revenue': 'revenue:ticket-finance', 'transfer': 'clearing:bank'})
class TicketPaymentLinkTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username='ticket-finance-admin', phone='+990870', password='test', role='admin',
        )
        self.waiter = User.objects.create_user(
            username='ticket-finance-waiter', phone='+990871', password='test', role='waiter',
        )
        FinancialAccount.objects.create(
            code='cash:ticket-finance', name_ar='صندوق',
            account_type=FinancialAccount.AccountType.ASSET, scope='cashbox',
            is_active=True,
            negative_balance_policy=FinancialAccount.NegativeBalancePolicy.ALLOW,
        )
        FinancialAccount.objects.create(
            code='revenue:ticket-finance', name_ar='إيراد',
            account_type=FinancialAccount.AccountType.REVENUE, scope='operating', is_active=True,
        )
        category = Category.objects.create(name_ar='تذاكر')
        self.ticket_product = Product.objects.create(
            category=category, name_ar='تذكرة فعالية',
            price_syp=1000, item_type=Product.ItemType.EVENT_TICKET,
            product_type=Product.ProductType.EVENT, requires_preparation=False,
        )
        self.food_product = Product.objects.create(
            category=category, name_ar='طعام', price_syp=1000,
            item_type=Product.ItemType.FOOD, product_type=Product.ProductType.FOOD,
        )
        self.event = Event.objects.create(
            title_ar='عرض اختبار', starts_at=timezone.now()+timedelta(days=1), capacity=10,
        )
        self.tier = EventTicketType.objects.create(
            event=self.event, name_ar='فئة', price_syp=1000, capacity=10,
        )
        self.booking = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='حجز للتذاكر',
            phone='+963800001', party_size=2, status='confirmed',
        ))
        set_event_ticket_allocations(
            self.booking.pk, {self.tier.pk: 2}, actor=self.manager,
        )
        self.order = self.make_order(self.ticket_product)

    def make_order(self, product, quantity=2):
        order = Order.objects.create()
        OrderItem.objects.create(
            order=order, product=product, quantity=quantity,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=product.price_syp,
            line_total_syp_snapshot=product.price_syp*quantity,
            selected_options_snapshot=[], prep_status=OrderItem.PrepStatus.NO_PREP,
        )
        return order

    def pay(self, order=None, amount=1000, key='ticket-link-1'):
        return collect(
            order or self.order,
            PostingContext(actor=self.manager, idempotency_key=key, channel='cashier'),
            amount, Payment.Method.CASH,
        )

    def test_link_existing_partial_and_full_receipts_without_new_postings(self):
        first = self.pay()
        before = (Payment.objects.count(), PostingBatch.objects.count())
        link, created = link_posted_ticket_payment(
            self.booking.pk, first.pk, actor=self.manager,
        )
        self.assertTrue(created)
        self.assertEqual((Payment.objects.count(), PostingBatch.objects.count()), before)
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 1000)
        self.assertEqual(ticket_payment_summary(self.booking)['outstanding'], 1000)
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'مدفوع جزئياً')
        again, created = link_posted_ticket_payment(
            self.booking.pk, first.pk, actor=self.manager,
        )
        self.assertFalse(created)
        self.assertEqual(link.pk, again.pk)
        second = self.pay(amount=1000, key='ticket-link-2')
        link_posted_ticket_payment(self.booking.pk, second.pk, actor=self.manager)
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'مدفوع بالكامل')
        self.assertEqual(ticket_payment_summary(self.booking)['outstanding'], 0)

    def test_unposted_receipt_and_non_ticket_item_rejected(self):
        fake = Payment.objects.create(
            order=self.order, amount_syp=250, method=Payment.Method.CASH,
        )
        with self.assertRaises(ValidationError):
            link_posted_ticket_payment(self.booking.pk, fake.pk, actor=self.manager)
        food_order = self.make_order(self.food_product)
        food_payment = self.pay(order=food_order, amount=500, key='ticket-food-pay')
        with self.assertRaises(ValidationError):
            link_posted_ticket_payment(self.booking.pk, food_payment.pk, actor=self.manager)

    def test_oversized_or_second_booking_link_is_rejected(self):
        receipt = self.pay(amount=1000)
        link_posted_ticket_payment(self.booking.pk, receipt.pk, actor=self.manager)
        other = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='حجز آخر',
            phone='+963800002', party_size=1, status='confirmed',
        ))
        set_event_ticket_allocations(other.pk, {self.tier.pk: 1}, actor=self.manager)
        with self.assertRaises(ValidationError):
            link_posted_ticket_payment(other.pk, receipt.pk, actor=self.manager)
        extra_order = self.make_order(self.ticket_product)
        extra_payment = self.pay(order=extra_order, amount=1500, key='ticket-excess')
        with self.assertRaises(ValidationError):
            link_posted_ticket_payment(self.booking.pk, extra_payment.pk, actor=self.manager)

    def test_unlinked_cashier_receipt_cannot_be_taken_from_another_booking_invoice(self):
        sale, _ = issue_ticket_order(self.booking.pk, actor=self.manager)
        # Generic cashier collection posts the receipt before manual attribution.
        receipt = self.pay(order=sale.order, amount=1000, key='owned-ticket-receipt')
        other = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='حجز اختبار آخر',
            phone='+963800003', party_size=1, status='confirmed',
        ))
        set_event_ticket_allocations(other.pk, {self.tier.pk: 1}, actor=self.manager)
        before = (Payment.objects.count(), PostingBatch.objects.count())
        self.assertFalse(ReservationTicketPaymentLink.objects.exists())

        # The destination has no invoice; the source invoice still owns its receipt.
        with self.assertRaisesMessage(ValidationError, 'فاتورة هذا السند تخص حجزاً آخر.'):
            link_posted_ticket_payment(other.pk, receipt.pk, actor=self.manager)
        self.assertFalse(ReservationTicketPaymentLink.objects.exists())
        self.assertEqual(ticket_payment_summary(other)['posted'], 0)
        self.assertEqual((Payment.objects.count(), PostingBatch.objects.count()), before)

        link, created = link_posted_ticket_payment(
            self.booking.pk, receipt.pk, actor=self.manager,
        )
        self.assertTrue(created)
        again, created = link_posted_ticket_payment(
            self.booking.pk, receipt.pk, actor=self.manager,
        )
        self.assertFalse(created)
        self.assertEqual(again.pk, link.pk)
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 1000)
        self.assertEqual((Payment.objects.count(), PostingBatch.objects.count()), before)

    def test_reversed_or_inactive_payment_no_longer_counted(self):
        receipt = self.pay()
        link_posted_ticket_payment(self.booking.pk, receipt.pk, actor=self.manager)
        Payment.objects.filter(pk=receipt.pk).update(is_active=False)
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 0)
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'غير مدفوع')

    def test_non_manager_cannot_link_receipt(self):
        receipt = self.pay()
        with self.assertRaises(ValidationError):
            link_posted_ticket_payment(self.booking.pk, receipt.pk, actor=self.waiter)
        self.assertFalse(ReservationTicketPaymentLink.objects.exists())

    def test_staff_detail_only_marks_real_journal_receipts_paid(self):
        receipt = self.pay()
        self.client.force_login(self.manager)
        detail_url = reverse('staff_reservation_detail', args=[self.booking.pk])
        self.assertContains(self.client.get(detail_url), 'غير مدفوع')
        url = reverse('staff_reservation_ticket_payment_link', args=[self.booking.pk])
        self.assertEqual(self.client.post(url, {'payment_id': receipt.pk}).status_code, 302)
        self.assertContains(self.client.get(detail_url), 'مدفوع جزئياً')
        self.assertEqual(ReservationTicketPaymentLink.objects.count(), 1)
