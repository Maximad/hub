"""Regression tests for explicit table-bill choice and standalone fast internet identification."""
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Category, HubVisit, HubVisitBrowserCredential, InternetPackage, InternetSession,
    Order, Product, Room, SystemSetting, TableArea,
)
from core.services.fast_internet_identity import normalize_standalone_identity
from core.services.visit_internet_devices import (
    create_visit_internet_sale_and_start, start_visit_metered_session,
)
from core.settings_helpers import get_system_settings


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FastInternetIdentityTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name_ar='خدمات')
        product = Product.objects.create(
            category=category, name_ar='إنترنت حسب الوقت', price_syp=0,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
            requires_preparation=False, visible_on_qr=False, orderable_on_qr=False,
            visible_on_pos=False, orderable_on_pos=False, not_discountable=True,
            track_margin=False,
        )
        SystemSetting.objects.create(
            customer_visits_enabled=True, customer_internet_self_service_enabled=True,
            internet_metered_enabled=True, default_rate_per_hour_syp=600,
            default_minimum_minutes=1, auto_create_order_for_metered_sessions=True,
            internet_service_product=product,
        )
        get_system_settings.cache_clear()
        self.table = TableArea.objects.create(
            room=Room.objects.create(name_ar='الصالة'), name_ar='طاولة 4',
        )

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_anonymous_direct_fast_start_requires_identity_before_creating_visit(self):
        response = self.client.post(reverse('wifi_entry'), {'wifi_action': 'start_fast_wifi'})
        self.assertRedirects(response, reverse('wifi_entry') + '?mode=fast')
        self.assertFalse(HubVisit.objects.exists())
        self.assertFalse(InternetSession.objects.exists())
        page = self.client.get(response['Location'])
        self.assertContains(page, 'أنا على طاولة')
        self.assertContains(page, 'name="guest_name"')
        self.assertContains(page, 'name="guest_phone"')

    def test_invalid_standalone_identity_is_rejected_without_creating_visit(self):
        for name, phone in [('', '0999123456'), ('رنا', ''), ('رنا', 'abc'), ('رنا', '12')]:
            result = self.client.post(reverse('wifi_entry'), {
                'wifi_action': 'start_fast_wifi', 'guest_name': name, 'guest_phone': phone,
            })
            self.assertEqual(result.status_code, 302)
        self.assertFalse(HubVisit.objects.exists())

    @patch('core.views.wifi.prepare_visit_metered_session_network', return_value=False)
    def test_standalone_direct_start_is_attached_to_named_visit(self, _network):
        response = self.client.post(reverse('wifi_entry'), {
            'wifi_action': 'start_fast_wifi',
            'guest_name': 'رنا شمس', 'guest_phone': '+963 999 111 222',
        })
        self.assertEqual(response.status_code, 302)
        visit = HubVisit.objects.get()
        session = InternetSession.objects.get()
        self.assertIsNone(visit.table_id)
        self.assertEqual((visit.guest_name, visit.guest_phone), ('رنا شمس', '+963 999 111 222'))
        self.assertEqual((session.guest_name, session.guest_phone),
                         ('رنا شمس', '+963 999 111 222'))
        self.assertEqual(session.visit_id, visit.pk)
        self.assertEqual(Order.objects.count(), 0)

    def test_table_number_route_must_select_an_account_not_attach_an_existing_visit(self):
        existing = HubVisit.objects.create(table=self.table)
        response = self.client.post(reverse('visit_internet_start'), {
            'mode': 'metered', 'table': str(self.table.qr_token),
        })
        self.assertRedirects(
            response,
            reverse('menu_table', kwargs={'qr_token': self.table.qr_token}) + '?choose=1',
        )
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertEqual(HubVisit.objects.get().pk, existing.pk)
        self.assertFalse(InternetSession.objects.exists())

    def test_table_accounts_can_be_separate_at_same_table(self):
        path = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})
        self.client.post(path, {'visit_action': 'create'})
        first = HubVisit.objects.get()
        browser2 = self.client_class()
        browser2.post(path, {'visit_action': 'create'})
        self.assertEqual(HubVisit.objects.count(), 2)
        self.assertEqual(HubVisit.objects.filter(table=self.table).count(), 2)
        self.assertNotEqual(HubVisit.objects.exclude(pk=first.pk).get().pk, first.pk)

    def test_shared_service_requires_identity_for_tableless_sessions_and_packages(self):
        visit = HubVisit.objects.create()
        credential = HubVisitBrowserCredential.objects.create(
            visit=visit, token_hash='a' * 64,
        )
        with self.assertRaises(ValidationError):
            start_visit_metered_session(visit=visit, credential=credential)
        self.assertFalse(InternetSession.objects.exists())
        self.assertFalse(visit.internet_sessions.exists())

        package = InternetPackage.objects.create(
            name_ar='باقة سريعة', code='identified-fast', price_syp=500,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60, visible_to_customer=True,
        )
        with self.assertRaises(ValidationError):
            create_visit_internet_sale_and_start(
                visit=visit, credential=credential, package=package, request_key='reject-anon',
            )
        self.assertFalse(Order.objects.exists())

        with self.assertRaises(ValidationError):
            start_visit_metered_session(
                visit=visit, credential=credential, guest_name='رنا',
                guest_phone='abc',
            )
        self.assertFalse(InternetSession.objects.exists())

        session, created = start_visit_metered_session(
            visit=visit, credential=credential,
            guest_name='رنا شمس', guest_phone='0999123456',
        )
        self.assertTrue(created)
        visit.refresh_from_db()
        self.assertEqual((visit.guest_name, visit.guest_phone), ('رنا شمس', '0999123456'))
        self.assertEqual(session.visit_id, visit.pk)
        second, is_new = start_visit_metered_session(visit=visit, credential=credential)
        self.assertFalse(is_new)
        self.assertEqual(second.pk, session.pk)

    def test_existing_contact_cannot_be_reassigned_silently(self):
        visit = HubVisit.objects.create(guest_name='رنا شمس', guest_phone='0999123456')
        credential = HubVisitBrowserCredential.objects.create(
            visit=visit, token_hash='b' * 64,
        )
        with self.assertRaises(ValidationError):
            start_visit_metered_session(
                visit=visit, credential=credential,
                guest_name='شخص آخر', guest_phone='0999123456',
            )
        self.assertFalse(InternetSession.objects.exists())

    def test_identification_does_not_affect_basic_wifi_entry_or_menu(self):
        self.assertEqual(self.client.get(reverse('wifi_entry')).status_code, 200)
        self.assertEqual(self.client.get(reverse('menu_public')).status_code, 200)
        self.assertFalse(HubVisit.objects.exists())

    @patch('core.views.wifi.prepare_guest_wifi_session_network', return_value=False)
    def test_fast_registration_form_remains_available_with_active_basic_session(self, _network):
        from internet.models import GuestWifiPolicy
        policy, _created = GuestWifiPolicy.objects.get_or_create(key='default')
        policy.enabled = True
        policy.require_venue_code = False
        policy.save()
        self.client.post(reverse('wifi_entry'), {'wifi_action': 'start_guest_wifi'})
        response = self.client.get(reverse('wifi_entry') + '?mode=fast')
        self.assertContains(response, 'name="guest_name"')
        self.assertContains(response, 'name="guest_phone"')
        self.assertContains(response, 'أنا على طاولة')

    def test_contact_normalization_accepts_local_and_international_numbers(self):
        self.assertEqual(
            normalize_standalone_identity('  رنا   شمس ', '+963 999 111 222'),
            ('رنا شمس', '+963 999 111 222'),
        )
