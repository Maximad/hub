from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import HubVisit, Member, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings
from reservations.models import Reservation
from reservations.services import check_in_reservation


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class ReservationCheckInVisitTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='arrival-admin', phone='94001', password='x', role='admin'
        )
        self.waiter = User.objects.create_user(
            username='arrival-waiter', phone='94002', password='x', role='waiter'
        )
        self.cashier = User.objects.create_user(
            username='arrival-cashier', phone='94003', password='x', role='cashier'
        )
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 9')
        self.other_table = TableArea.objects.create(room=self.room, name_ar='طاولة 10')
        self.reservation = Reservation.objects.create(
            reservation_type=Reservation.ReservationType.REGULAR,
            name='ضيف الحجز',
            phone='0999000001',
            table_area=self.table,
            room=self.room,
            reservation_date=timezone.localdate(),
            start_time=time(10, 0),
            end_time=time(12, 0),
            party_size=2,
            status=Reservation.Status.CONFIRMED,
            notes='قرب النافذة',
            created_by=self.admin,
        )
        SystemSetting.objects.create()
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_confirmed_reservation_checkin_creates_and_links_visit(self):
        visit, created = check_in_reservation(
            self.reservation.pk,
            actor=self.waiter,
            table_id=self.table.pk,
        )

        self.assertTrue(created)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.visit_id, visit.pk)
        self.assertEqual(visit.table_id, self.table.pk)
        self.assertEqual(visit.created_by_id, self.waiter.pk)
        self.assertEqual(visit.notes, self.reservation.notes)

    def test_repeated_checkin_returns_same_open_visit(self):
        first, first_created = check_in_reservation(
            self.reservation.pk, actor=self.waiter, table_id=self.table.pk
        )
        second, second_created = check_in_reservation(
            self.reservation.pk, actor=self.waiter, table_id=self.table.pk
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(HubVisit.objects.count(), 1)

    def test_pending_reservation_cannot_check_in(self):
        self.reservation.status = Reservation.Status.PENDING
        self.reservation.save(update_fields=['status', 'updated_at'])
        self.client.force_login(self.waiter)

        response = self.client.post(
            reverse('staff_reservation_detail', args=[self.reservation.pk]),
            {'action': 'checkin', 'table': self.table.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HubVisit.objects.count(), 0)
        self.reservation.refresh_from_db()
        self.assertIsNone(self.reservation.visit_id)

    def test_occupied_table_does_not_create_second_open_visit(self):
        occupied = HubVisit.objects.create(table=self.table, created_by=self.admin)
        self.client.force_login(self.waiter)

        response = self.client.post(
            reverse('staff_reservation_detail', args=[self.reservation.pk]),
            {'action': 'checkin', 'table': self.table.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertTrue(HubVisit.objects.filter(pk=occupied.pk).exists())
        self.reservation.refresh_from_db()
        self.assertIsNone(self.reservation.visit_id)

    def test_exact_phone_match_links_existing_member(self):
        member = Member.objects.create(name_ar='عضو معروف', phone=self.reservation.phone)

        visit, _created = check_in_reservation(
            self.reservation.pk,
            actor=self.waiter,
            table_id=self.table.pk,
        )

        self.assertEqual(visit.member_id, member.pk)

    def test_reservation_detail_exposes_checkin_then_visit_actions(self):
        self.client.force_login(self.waiter)
        detail_url = reverse('staff_reservation_detail', args=[self.reservation.pk])

        before = self.client.get(detail_url)
        self.assertEqual(before.status_code, 200)
        self.assertContains(before, 'تسجيل الوصول وفتح الجلسة')
        self.assertContains(before, 'مؤكد — بانتظار الوصول')

        response = self.client.post(
            detail_url,
            {'action': 'checkin', 'table': self.table.pk},
        )
        self.reservation.refresh_from_db()
        self.assertIsNotNone(self.reservation.visit_id)
        expected = f"{reverse('staff_home')}?visit={self.reservation.visit.public_code}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

        after = self.client.get(detail_url)
        self.assertContains(after, 'الضيف داخل هَبّ الآن')
        self.assertContains(after, 'فتح في مساحة العمليات')
        self.assertContains(after, '+ طلب لهذه الجلسة')
        self.assertNotContains(after, 'تسجيل الوصول وفتح الجلسة')

    def test_waiter_can_check_in_but_cashier_cannot(self):
        detail_url = reverse('staff_reservation_detail', args=[self.reservation.pk])
        self.client.force_login(self.cashier)
        response = self.client.post(
            detail_url,
            {'action': 'checkin', 'table': self.table.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(HubVisit.objects.count(), 0)

        self.client.force_login(self.waiter)
        response = self.client.post(
            detail_url,
            {'action': 'checkin', 'table': self.table.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(HubVisit.objects.count(), 1)

    def test_closing_linked_visit_completes_reservation(self):
        visit, _created = check_in_reservation(
            self.reservation.pk,
            actor=self.waiter,
            table_id=self.table.pk,
        )
        self.client.force_login(self.waiter)

        response = self.client.post(
            reverse('staff_visit_detail', args=[visit.public_code]),
            {'action': 'close'},
        )

        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.reservation.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.CLOSED)
        self.assertEqual(self.reservation.status, Reservation.Status.COMPLETED)
        self.assertEqual(self.reservation.operational_status_label, 'انتهت الجلسة')

    def test_workspace_script_supports_checkin_visit_redirect(self):
        visit, _created = check_in_reservation(
            self.reservation.pk,
            actor=self.waiter,
            table_id=self.table.pk,
        )
        self.client.force_login(self.waiter)

        response = self.client.get(reverse('staff_home'), {'visit': str(visit.public_code)})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(visit.public_code))
        self.assertContains(response, 'js/staff_workspace.js')
