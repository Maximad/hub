from concurrent.futures import ThreadPoolExecutor
from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from core.models import AuditEvent, CancellationReason, Order, Room
from reservations.models import Reservation


class StatusTransitionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username='transition-admin', phone='10001', password='x', role='admin')
        self.waiter = User.objects.create_user(username='transition-waiter', phone='10002', password='x', role='waiter')
        self.room = Room.objects.create(name_ar='Transition room')

    def test_every_declared_order_transition_is_allowed(self):
        for old, destinations in Order.STATUS_TRANSITIONS.items():
            for new in destinations:
                order = Order.objects.create(status=old)
                reason = CancellationReason.OTHER if new == Order.Status.CANCELLED else ''
                changed = Order.transition_status(order.pk, actor=self.waiter, new_status=new, cancellation_reason=reason)
                self.assertEqual(changed.status, new)

    def test_every_declared_delivery_transition_is_allowed(self):
        for old, destinations in Order.DELIVERY_TRANSITIONS.items():
            for new in destinations:
                order = Order.objects.create(fulfillment_mode=Order.FulfillmentMode.DELIVERY, delivery_status=old)
                reason = CancellationReason.OTHER if new == Order.DeliveryStatus.CANCELLED else ''
                changed = Order.transition_delivery_status(order.pk, actor=self.waiter, new_status=new, cancellation_reason=reason)
                self.assertEqual(changed.delivery_status, new)

    def test_reservation_transitions_and_backward_jump(self):
        for old, destinations in Reservation.TRANSITIONS.items():
            for new in destinations:
                reservation = Reservation.objects.create(
                    name='R', phone='1', status=old, room=self.room,
                    reservation_date=date(2026, 8, 12), start_time=time(10), end_time=time(11),
                )
                self.assertEqual(Reservation.transition_status(reservation.pk, actor=self.waiter, new_status=new).status, new)
        reservation = Reservation.objects.create(name='R2', phone='2', status=Reservation.Status.CONFIRMED)
        with self.assertRaises(ValidationError):
            Reservation.transition_status(reservation.pk, actor=self.waiter, new_status=Reservation.Status.PENDING)

    def test_terminal_correction_requires_admin_and_reason_and_is_audited(self):
        order = Order.objects.create(status=Order.Status.CANCELLED, cancellation_reason=CancellationReason.OTHER,
                                     cancelled_by=self.waiter)
        with self.assertRaises(ValidationError):
            Order.correct_status(order.pk, actor=self.waiter, new_status=Order.Status.NEW, reason='mistake')
        with self.assertRaises(ValidationError):
            Order.correct_status(order.pk, actor=self.admin, new_status=Order.Status.NEW, reason='')
        changed = Order.correct_status(order.pk, actor=self.admin, new_status=Order.Status.NEW, reason='duplicate record')
        self.assertIsNone(changed.cancelled_at)
        self.assertEqual(changed.cancellation_reason, '')
        event = AuditEvent.objects.get(action='order_status_correction', source_object_id=str(order.pk))
        self.assertEqual((event.actor, event.before_snapshot['status'], event.after_snapshot['status']),
                         (self.admin, Order.Status.CANCELLED, Order.Status.NEW))
        self.assertEqual(event.after_snapshot['reason'], 'duplicate record')
        with self.assertRaises(ValidationError):
            event.save()

    def test_delivery_correction_clears_terminal_timestamp(self):
        order = Order.objects.create(fulfillment_mode=Order.FulfillmentMode.DELIVERY,
                                     delivery_status=Order.DeliveryStatus.OUT_FOR_DELIVERY)
        order = Order.transition_delivery_status(order.pk, actor=self.waiter, new_status=Order.DeliveryStatus.DELIVERED)
        self.assertIsNotNone(order.delivery_delivered_at)
        order = Order.correct_delivery_status(order.pk, actor=self.admin,
                                              new_status=Order.DeliveryStatus.OUT_FOR_DELIVERY, reason='driver correction')
        self.assertIsNone(order.delivery_delivered_at)

    def test_delivery_cancellation_requires_reason_and_correction_clears_fields(self):
        order = Order.objects.create(
            fulfillment_mode=Order.FulfillmentMode.DELIVERY,
            delivery_status=Order.DeliveryStatus.OUT_FOR_DELIVERY,
        )
        with self.assertRaises(ValidationError):
            Order.transition_delivery_status(
                order.pk, actor=self.waiter, new_status=Order.DeliveryStatus.CANCELLED,
            )
        order = Order.transition_delivery_status(
            order.pk, actor=self.waiter, new_status=Order.DeliveryStatus.CANCELLED,
            cancellation_reason=CancellationReason.OTHER, cancellation_notes='No answer',
        )
        self.assertEqual(order.cancelled_by, self.waiter)
        self.assertEqual(order.cancellation_notes, 'No answer')
        self.assertIsNotNone(order.delivery_cancelled_at)
        order = Order.correct_delivery_status(
            order.pk, actor=self.admin, new_status=Order.DeliveryStatus.OUT_FOR_DELIVERY,
            reason='Customer answered',
        )
        self.assertEqual(order.cancellation_reason, '')
        self.assertEqual(order.cancellation_notes, '')
        self.assertIsNone(order.cancelled_by)
        self.assertIsNone(order.delivery_cancelled_at)

    def test_reservation_terminal_correction_requires_admin_and_reason(self):
        reservation = Reservation.objects.create(
            name='Correct me', phone='3', status=Reservation.Status.CANCELLED,
        )
        with self.assertRaises(ValidationError):
            Reservation.correct_status(
                reservation.pk, actor=self.waiter,
                new_status=Reservation.Status.PENDING, reason='mistake',
            )
        with self.assertRaises(ValidationError):
            Reservation.correct_status(
                reservation.pk, actor=self.admin,
                new_status=Reservation.Status.PENDING, reason='',
            )


class ConcurrentStatusTransitionTests(TransactionTestCase):
    reset_sequences = True

    def test_only_one_identical_update_wins_from_locked_row(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite does not provide row-level SELECT FOR UPDATE semantics.')
        User = get_user_model()
        actor = User.objects.create_user(username='concurrent-waiter', phone='10003', password='x', role='waiter')
        order = Order.objects.create()

        def advance():
            close_old_connections()
            try:
                Order.transition_status(order.pk, actor=actor, new_status=Order.Status.ACCEPTED)
                return 'changed'
            except ValidationError:
                return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: advance(), range(2)))
        self.assertCountEqual(results, ['changed', 'rejected'])
        self.assertEqual(AuditEvent.objects.filter(action='order_status_transition', source_object_id=str(order.pk)).count(), 1)
