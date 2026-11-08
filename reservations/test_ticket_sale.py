"""Ticket-only orders reuse POS and finance. No separate pseudo-payment system."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Category, FinancialAccount, HubVisit, Order, OrderItem, Payment, PostingBatch, Product,
)
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect
from events.models import Event, EventTicketType
from reservations.finance_links import ticket_payment_summary
from reservations.models import Reservation, ReservationTicketSale
from reservations.services import create_reservation, change_reservation_status
from reservations.ticketing import set_event_ticket_allocations
from reservations.ticket_sale import create_event_ticket_order


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventTicketSaleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username='ticket-cashier-admin', phone='+990931', role='admin', password='test',
        )
        self.waiter = User.objects.create_user(
            username='ticket-cashier-waiter', phone='+990932', role='waiter', password='test',
        )
        FinancialAccount.objects.create(
            code='cash:ticket-sale', name_ar='صندوق التذاكر',
            account_type=FinancialAccount.AccountType.ASSET,
            scope='cashbox', is_active=True,
            negative_balance_policy=FinancialAccount.NegativeBalancePolicy.ALLOW,
        )
        FinancialAccount.objects.create(
            code='revenue:ticket-sale', name_ar='إيراد التذاكر',
            account_type=FinancialAccount.AccountType.REVENUE,
            scope='operating', is_active=True,
        )
        self.event = Event.objects.create(
            title_ar='حفلة اختبار', starts_at=timezone.now()+timedelta(days=1),
            capacity=10, status='published',
        )
        self.front = EventTicketType.objects.create(
            event=self.event, name_ar='قريبة', price_syp=1000, capacity=5,
        )
        self.back = EventTicketType.objects.create(
            event=self.event, name_ar='بعيدة', price_syp=750, capacity=5,
        )
        self.booking = create_reservation(Reservation(
            event=self.event, reservation_type='event',
            name='زائر', phone='+963999888111', party_size=3, status='confirmed',
        ))
        set_event_ticket_allocations(
            self.booking.pk,
            {self.front.pk: 1, self.back.pk: 2}, actor=self.manager,
        )

    def test_sale_creates_one_ticket_only_order_without_payment_or_hub_visit(self):
        before = (Payment.objects.count(), PostingBatch.objects.count(), HubVisit.objects.count())
        sale, created = create_event_ticket_order(self.booking.pk, actor=self.manager)
        self.assertTrue(created)
        self.assertEqual(sale.order.total_syp, 2500)
        self.assertEqual(sale.order.items.count(), 2)
        self.assertEqual(sale.order.items.filter(
            product__item_type=Product.ItemType.EVENT_TICKET,
            prep_status=OrderItem.PrepStatus.NO_PREP,
            product__visible_on_qr=False,
        ).count(), 2)
        self.assertEqual((Payment.objects.count(), PostingBatch.objects.count(), HubVisit.objects.count()), before)
        self.assertIsNone(Reservation.objects.get(pk=self.booking.pk).visit_id)
        again, created = create_event_ticket_order(self.booking.pk, actor=self.manager)
        self.assertFalse(created)
        self.assertEqual(again.order_id, sale.order_id)
        self.assertEqual(ReservationTicketSale.objects.count(), 1)
        self.front.refresh_from_db()
        self.assertEqual(self.front.product.item_type, Product.ItemType.EVENT_TICKET)
        self.assertFalse(self.front.product.visible_on_pos)
        self.assertFalse(self.front.product.visible_on_qr)

    def test_actual_posted_cashier_payment_updates_booking_automatically(self):
        sale, _ = create_event_ticket_order(self.booking.pk, actor=self.manager)
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'غير مدفوع')
        collect(
            sale.order,
            PostingContext(actor=self.manager, idempotency_key='ticket-order-real-1', channel='cashier'),
            1000, Payment.Method.CASH,
        )
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'مدفوع جزئياً')
        collect(
            sale.order,
            PostingContext(actor=self.manager, idempotency_key='ticket-order-real-2', channel='cashier'),
            1500, Payment.Method.CASH,
        )
        self.assertEqual(ticket_payment_summary(self.booking)['label'], 'مدفوع بالكامل')
        self.assertEqual(ticket_payment_summary(self.booking)['posted'], 2500)
        self.assertEqual(PostingBatch.objects.filter(operation_type='order_payment.collect').count(), 2)

    def test_cannot_change_ticket_tiers_after_sale_issued(self):
        create_event_ticket_order(self.booking.pk, actor=self.manager)
        with self.assertRaises(ValidationError):
            set_event_ticket_allocations(
                self.booking.pk, {self.front.pk: 2, self.back.pk: 1},
                actor=self.manager,
            )

    def test_cancel_unpaid_sale_before_booking_cancellation(self):
        from core.models import CancellationReason
        sale, _ = create_event_ticket_order(self.booking.pk, actor=self.manager)
        with self.assertRaises(ValidationError):
            change_reservation_status(self.booking.pk, 'cancelled', actor=self.manager)
        Order.transition_status(
            sale.order_id, actor=self.manager, new_status=Order.Status.CANCELLED,
            cancellation_reason=CancellationReason.CUSTOMER_CHANGED_MIND,
        )
        change_reservation_status(self.booking.pk, 'cancelled', actor=self.manager)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'cancelled')

    def test_paid_ticket_cannot_be_cancelled_without_refund(self):
        sale, _ = create_event_ticket_order(self.booking.pk, actor=self.manager)
        collect(
            sale.order,
            PostingContext(actor=self.manager, idempotency_key='ticket-cancel-1', channel='cashier'),
            1000, Payment.Method.CASH,
        )
        with self.assertRaises(ValidationError):
            change_reservation_status(self.booking.pk, 'cancelled', actor=self.manager)

    def test_cancelled_show_blocks_new_cashier_collection(self):
        from core.services.posting.exceptions import InvalidTransition
        sale, _ = create_event_ticket_order(self.booking.pk, actor=self.manager)
        self.event.status = Event.Status.CANCELLED
        self.event.save(update_fields=['status'])
        before = Payment.objects.count()
        with self.assertRaises(InvalidTransition):
            collect(
                sale.order,
                PostingContext(actor=self.manager, idempotency_key='ticket-after-cancel', channel='cashier'),
                1000, Payment.Method.CASH,
            )
        self.assertEqual(Payment.objects.count(), before)

    def test_non_cashier_cannot_issue_and_food_is_not_treated_as_ticket(self):
        with self.assertRaises(ValidationError):
            create_event_ticket_order(self.booking.pk, actor=self.waiter)
        cat = Category.objects.create(name_ar='مشاريب')
        product = Product.objects.create(
            category=cat, name_ar='مشروب غير محدد',
            price_syp=1000, item_type=Product.ItemType.BEVERAGE,
        )
        self.front.product = product
        self.front.save(update_fields=['product'])
        with self.assertRaises(ValidationError):
            create_event_ticket_order(self.booking.pk, actor=self.manager)
        self.assertEqual(ReservationTicketSale.objects.count(), 0)

    def test_staff_button_issues_order_and_redirects_to_existing_cashier(self):
        self.client.force_login(self.manager)
        detail = self.client.get(reverse('staff_reservation_detail', args=[self.booking.pk]))
        self.assertContains(detail, 'إنشاء طلب تذاكر في الكاشير')
        url = reverse('staff_reservation_create_ticket_order', args=[self.booking.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        sale = ReservationTicketSale.objects.get(reservation=self.booking)
        self.assertIn(str(sale.order.public_code), response.url)
        self.assertContains(
            self.client.get(reverse('staff_reservation_detail', args=[self.booking.pk])),
            'فتح طلب التذاكر في الكاشير',
        )
