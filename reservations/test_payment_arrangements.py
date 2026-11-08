from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from reservations.arrangements import set_payment_arrangement
from reservations.models import Reservation, ReservationPaymentArrangement
from reservations.services import create_reservation


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ReservationPaymentArrangementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username='arrangement-admin', phone='+990787', password='test', role='admin',
        )
        self.waiter = User.objects.create_user(
            username='arrangement-waiter', phone='+990788', password='test', role='waiter',
        )
        self.event = Event.objects.create(
            title_ar='عرض', starts_at=timezone.now() + timedelta(days=3), capacity=4,
        )
        self.booking = create_reservation(Reservation(
            reservation_type='event', event=self.event, name='حجز تجريبي',
            phone='+9635001000', party_size=2, status='pending',
        ))

    def test_pay_at_door_can_confirm_but_does_not_post_payment(self):
        from core.models import Payment, PostingBatch
        before = (Payment.objects.count(), PostingBatch.objects.count())
        record = set_payment_arrangement(
            self.booking.pk, actor=self.waiter, mode='pay_at_door', confirm=True,
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'confirmed')
        self.assertEqual(record.mode, 'pay_at_door')
        self.assertIsNone(record.guaranteed_by_id)
        self.assertEqual((Payment.objects.count(), PostingBatch.objects.count()), before)

    def test_manager_can_guarantee_unpaid_booking_with_reason(self):
        due = timezone.now() + timedelta(days=2)
        record = set_payment_arrangement(
            self.booking.pk, actor=self.manager,
            mode='staff_guarantee', reason='ضمان الإدارة للحجز', due_at=due,
            confirm=True,
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'confirmed')
        self.assertEqual(record.guaranteed_by_id, self.manager.pk)
        self.assertEqual(record.due_at, due)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('staff_reservation_detail', args=[self.booking.pk]))
        self.assertContains(response, 'اتفاق السداد')
        self.assertContains(response, 'ضمان الإدارة')
        self.assertContains(response, 'الضامن')
        self.assertContains(response, 'غير موثّقة')

    def test_waiter_cannot_guarantee_or_revoke_manager_guarantee(self):
        with self.assertRaises(ValidationError):
            set_payment_arrangement(
                self.booking.pk, actor=self.waiter,
                mode='staff_guarantee', reason='غير مصرح',
            )
        set_payment_arrangement(
            self.booking.pk, actor=self.manager,
            mode='staff_guarantee', reason='موافقة مدير',
        )
        with self.assertRaises(ValidationError):
            set_payment_arrangement(
                self.booking.pk, actor=self.waiter, mode='pay_at_door',
            )
        self.assertEqual(
            self.booking.payment_arrangement.mode, 'staff_guarantee',
        )

    def test_invalid_expiry_or_missing_reason_rejected(self):
        with self.assertRaises(ValidationError):
            set_payment_arrangement(
                self.booking.pk, actor=self.manager,
                mode='staff_guarantee', reason='',
            )
        with self.assertRaises(ValidationError):
            set_payment_arrangement(
                self.booking.pk, actor=self.manager,
                mode='staff_guarantee', reason='موافقة مؤقتة',
                due_at=timezone.now() - timedelta(minutes=1),
            )

    def test_failed_confirmation_does_not_store_guarantee(self):
        create_reservation(Reservation(
            reservation_type='event', event=self.event, name='حجز سابق',
            phone='+9635002000', party_size=3, status='confirmed',
        ))
        with self.assertRaises(ValidationError):
            set_payment_arrangement(
                self.booking.pk, actor=self.manager,
                mode='staff_guarantee', reason='رغم السعة',
                confirm=True,
            )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'pending')
        self.assertFalse(
            ReservationPaymentArrangement.objects.filter(reservation=self.booking).exists()
        )

    def test_staff_endpoint_guarantee_requires_manager_and_csrf_safe_post(self):
        url = reverse('staff_reservation_payment_arrangement', args=[self.booking.pk])
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {
            'mode': 'staff_guarantee', 'reason': 'لا تملك صلاحية',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ReservationPaymentArrangement.objects.filter(reservation=self.booking).exists()
        )
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(url, {
            'mode': 'staff_guarantee',
            'reason': 'الحجز مضمون من الإدارة',
            'confirm': '1',
        }).status_code, 302)
        self.assertTrue(
            ReservationPaymentArrangement.objects.filter(
                reservation=self.booking, mode='staff_guarantee',
            ).exists()
        )
