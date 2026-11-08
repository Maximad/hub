"""Phase 1 event workspace presentation regression tests.

Ticket sales, paid status, public requests and check-in remain unchanged.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from events.models import Event, EventMedia, EventTicketType
from reservations.models import Reservation


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    PUBLIC_HOMEPAGE_ENABLED=True,
)
class StaffEventWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(
            username='events-workspace-admin',
            phone='9855678',
            password='test',
            role='admin',
        )
        cls.event = Event.objects.create(
            title_ar='ليلة تجريبية',
            starts_at=timezone.now() + timedelta(days=5),
            capacity=24,
            is_public=True,
            status=Event.Status.PUBLISHED,
        )
        EventTicketType.objects.create(
            event=cls.event,
            name_ar='قريب من المسرح',
            price_syp=1000,
            capacity=12,
            is_active=True,
        )
        Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=cls.event,
            name='حجز مؤكد',
            phone='+963900000001',
            party_size=3,
            status=Reservation.Status.CONFIRMED,
        )
        Reservation.objects.create(
            reservation_type=Reservation.ReservationType.EVENT,
            event=cls.event,
            name='بانتظار التأكيد',
            phone='+963900000002',
            party_size=2,
            status=Reservation.Status.PENDING,
        )

    def setUp(self):
        self.client.force_login(self.manager)

    def test_list_shows_arabic_cards_and_no_missing_image_breakage(self):
        response = self.client.get(reverse('staff_events'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'events-ui__card')
        self.assertContains(response, 'ليلة تجريبية')
        self.assertContains(response, 'منشورة')
        self.assertContains(response, 'events-ui__fallback')
        self.assertContains(response, reverse('staff_event_detail', args=[self.event.pk]))
        self.assertContains(response, 'staff_events_workspace.css')

    def test_staff_media_uses_existing_active_cover(self):
        media = MediaAsset.objects.create(
            title_ar='ملصق معتمد',
            media_type=MediaAsset.MediaType.IMAGE,
            external_url='https://example.org/hub-test-event.jpg',
        )
        EventMedia.objects.create(
            event=self.event,
            media_asset=media,
            role=EventMedia.Role.COVER,
            is_active=True,
            is_primary=True,
        )
        response = self.client.get(reverse('staff_events'))
        self.assertContains(response, 'https://example.org/hub-test-event.jpg')
        detail = self.client.get(reverse('staff_event_detail', args=[self.event.pk]))
        self.assertContains(detail, 'https://example.org/hub-test-event.jpg')

    def test_detail_displays_true_reservation_counts_and_ticket_data(self):
        response = self.client.get(reverse('staff_event_detail', args=[self.event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['confirmed_guests'], 3)
        self.assertEqual(response.context['pending_guests'], 2)
        self.assertEqual(response.context['reservation_count'], 2)
        self.assertContains(response, 'مقاعد الحجوزات المؤكدة')
        self.assertContains(response, 'قريب من المسرح')
        self.assertContains(response, '1000')
        self.assertContains(response, 'ليست بيانات تذاكر مدفوعة')
        self.assertContains(response, reverse('public_event_detail', args=[self.event.uuid]))
        self.assertContains(response, reverse('staff_reservation_detail', args=[Reservation.objects.first().pk]))

    def test_private_event_has_no_public_link(self):
        self.event.is_public = False
        self.event.save(update_fields=['is_public'])
        response = self.client.get(reverse('staff_event_detail', args=[self.event.pk]))
        self.assertContains(response, 'غير ظاهرة على الموقع')
        self.assertNotContains(response, reverse('public_event_detail', args=[self.event.uuid]))

    def test_anonymous_cannot_access_staff_event_details(self):
        self.client.logout()
        response = self.client.get(reverse('staff_event_detail', args=[self.event.pk]))
        self.assertEqual(response.status_code, 302)

    def test_creation_form_has_arabic_status_options(self):
        response = self.client.get(reverse('staff_event_new'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'إضافة فعالية')
        self.assertContains(response, 'مسودة')
        self.assertContains(response, 'منشورة')
