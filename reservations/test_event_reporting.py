from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventTicketType
from reservations.models import Reservation
from reservations.reporting import event_operations_report
from reservations.services import create_reservation
from reservations.ticketing import set_event_ticket_allocations
from reservations.admissions import set_event_admission_total


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventOperationsReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username='event-report-admin', phone='+990951', password='test', role='admin',
        )
        self.waiter = User.objects.create_user(
            username='event-report-waiter', phone='+990952', password='test', role='waiter',
        )
        self.event = Event.objects.create(
            title_ar='تقرير تجريبي', starts_at=timezone.now(), capacity=12,
        )
        self.tier = EventTicketType.objects.create(
            event=self.event, name_ar='حجز أمامي', price_syp=1000, capacity=8,
        )
        self.booking = create_reservation(Reservation(
            event=self.event, reservation_type='event',
            name='ضيف رقم 1', phone='+963900000001',
            party_size=3, status='confirmed',
        ))
        set_event_ticket_allocations(
            self.booking.pk, {self.tier.pk: 3}, actor=self.manager,
        )
        create_reservation(Reservation(
            event=self.event, reservation_type='event',
            name='ضيف رقم 2', phone='+963900000002',
            party_size=2, status='pending',
        ))

    def test_report_accounts_by_tier_and_explains_no_profit(self):
        set_event_admission_total(self.booking.pk, 2, actor=self.manager)
        values = event_operations_report(self.event)
        self.assertEqual(values['statuses']['confirmed'], 3)
        self.assertEqual(values['statuses']['pending'], 2)
        self.assertEqual(values['tier_total'], 3)
        self.assertEqual(values['nominal_total'], 3000)
        self.assertEqual(values['unclassified'], 2)
        self.assertEqual(values['arrived'], 2)
        self.assertEqual(values['posted_total'], 0)
        self.assertFalse(values['profit_available'])

    def test_reports_restricted_to_manager_and_do_not_show_guest_contacts(self):
        url = reverse('staff_event_operations_report', args=[self.event.pk])
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الربحية')
        self.assertContains(response, 'غير قابلة للحساب')
        self.assertNotContains(response, '+963900000001')
        self.assertNotContains(response, '+963900000002')
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
