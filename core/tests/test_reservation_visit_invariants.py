from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import HubVisit, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings
from reservations.models import Reservation
from reservations.services import check_in_reservation


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class ReservationVisitInvariantTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='visit-invariant-admin', phone='94101', password='x', role='admin'
        )
        self.waiter = User.objects.create_user(
            username='visit-invariant-waiter', phone='94102', password='x', role='waiter'
        )
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 11')
        self.reservation = Reservation.objects.create(
            reservation_type=Reservation.ReservationType.REGULAR,
            name='ضيف',
            phone='0999000011',
            table_area=self.table,
            room=self.room,
            reservation_date=timezone.localdate(),
            start_time=time(14, 0),
            end_time=time(16, 0),
            status=Reservation.Status.CONFIRMED,
            created_by=self.admin,
        )
        SystemSetting.objects.create()
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_open_linked_visit_blocks_direct_reservation_status_change(self):
        visit, _created = check_in_reservation(
            self.reservation.pk, actor=self.waiter, table_id=self.table.pk
        )
        self.assertEqual(visit.status, HubVisit.Status.OPEN)

        with self.assertRaises(ValidationError):
            Reservation.transition_status(
                self.reservation.pk,
                actor=self.waiter,
                new_status=Reservation.Status.CANCELLED,
            )

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)

    def test_closed_linked_visit_cannot_be_rechecked_in(self):
        visit, _created = check_in_reservation(
            self.reservation.pk, actor=self.waiter, table_id=self.table.pk
        )
        visit.status = HubVisit.Status.CLOSED
        visit.closed_at = timezone.now()
        visit.save(update_fields=['status', 'closed_at', 'updated_at'])

        with self.assertRaises(ValidationError):
            check_in_reservation(
                self.reservation.pk, actor=self.waiter, table_id=self.table.pk
            )

        self.assertEqual(HubVisit.objects.count(), 1)

    def test_future_reservation_cannot_check_in_early(self):
        self.reservation.reservation_date = timezone.localdate() + timedelta(days=1)
        self.reservation.save(update_fields=['reservation_date', 'updated_at'])

        with self.assertRaises(ValidationError):
            check_in_reservation(
                self.reservation.pk, actor=self.waiter, table_id=self.table.pk
            )

        self.assertEqual(HubVisit.objects.count(), 0)

    def test_manual_visit_creation_reuses_existing_open_table_visit(self):
        existing = HubVisit.objects.create(table=self.table, created_by=self.admin)
        self.client.force_login(self.waiter)

        response = self.client.post(
            reverse('staff_visits'),
            {'table': self.table.pk, 'notes': 'محاولة ثانية'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertRedirects(
            response,
            reverse('staff_visit_detail', args=[existing.public_code]),
            fetch_redirect_response=False,
        )
