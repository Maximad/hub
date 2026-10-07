from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    HubVisit,
    InternetBandwidthProfile,
    InternetSession,
    Order,
    Payment,
    SystemSetting,
)
from core.services.internet_internal_access import grant_internal_access
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionBrowserBinding


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_ENABLED=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class StaffInternalWifiFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff_user = User.objects.create_user(
            username='staff-wifi',
            first_name='سلوى',
            password='pass',
            phone='+963900008001',
            role=User.Role.WAITER,
        )
        self.other_user = User.objects.create_user(
            username='other-staff',
            first_name='ثامر',
            password='pass',
            phone='+963900008002',
            role=User.Role.WAITER,
        )
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        self.profile = InternetBandwidthProfile.objects.create(
            code='fast',
            name='Fast',
            router_profile_name='hub-full',
            is_active=True,
        )
        self.entitlement = grant_internal_access(
            staff_user=self.staff_user,
            grant_kind='team',
            bandwidth_profile=self.profile,
            access_mode='unlimited',
            validity_value=30,
            validity_unit='days',
            max_concurrent_devices=2,
            max_registered_devices=4,
            actor=self.staff_user,
        )

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_non_admin_staff_can_sign_in_from_captive_portal(self):
        self.assertFalse(self.staff_user.is_staff)

        landing = self.client.get(reverse('wifi_entry'), {'mode': 'internet'})
        self.assertContains(landing, reverse('wifi_staff_login'))
        self.assertContains(landing, 'دخول فريق هَبّ')

        login_page = self.client.get(reverse('wifi_staff_login'))
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, 'دخول فريق هَبّ')

        signed_in = self.client.post(reverse('wifi_staff_login'), {
            'username': self.staff_user.username,
            'password': 'pass',
        })
        self.assertEqual(signed_in.status_code, 302)
        self.assertEqual(
            signed_in['Location'],
            reverse('current_visit') + '?focus=internet',
        )
        self.assertEqual(InternetSession.objects.count(), 1)

    def test_authenticated_staff_sees_private_grant_as_primary_wifi_option(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse('wifi_entry'), {'mode': 'internet'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اتصال إنترنت الفريق')
        self.assertContains(response, 'value="start_staff_wifi"')
        self.assertContains(response, 'منحتك الداخلية جاهزة')
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)

    def test_other_staff_does_not_see_someone_elses_internal_grant(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse('wifi_entry'), {'mode': 'internet'})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="start_staff_wifi"')
        self.assertNotContains(response, 'اتصال إنترنت الفريق')

    def test_staff_wifi_start_binds_internal_grant_to_this_browser_without_sale(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse('wifi_entry'),
            {'wifi_action': 'start_staff_wifi'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('current_visit') + '?focus=internet',
        )
        self.assertIn('hub_visit', self.client.cookies)

        session = InternetSession.objects.get()
        self.assertEqual(session.entitlement_id, self.entitlement.pk)
        self.assertEqual(session.started_by_id, self.staff_user.pk)
        self.assertEqual(session.visit_id, HubVisit.objects.get().pk)
        binding = InternetSessionBrowserBinding.objects.get(session=session)
        self.assertEqual(binding.credential.visit_id, session.visit_id)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

        page = self.client.get(reverse('current_visit') + '?focus=internet')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'إنترنت الفريق')

    def test_direct_entitlement_endpoint_rechecks_staff_ownership(self):
        self.client.force_login(self.other_user)
        opened = self.client.post(
            reverse('wifi_entry'),
            {'wifi_action': 'internet_options'},
        )
        self.assertEqual(opened.status_code, 302)

        response = self.client.post(
            reverse(
                'visit_internet_entitlement_start',
                kwargs={'public_code': self.entitlement.public_code},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الباقة غير متاحة')
        self.assertEqual(InternetSession.objects.count(), 0)

    @patch('core.views.visits.build_hotspot_login_payload')
    @patch('core.views.wifi.one_tap_connect_configured', return_value=True)
    def test_staff_wifi_one_tap_relays_to_hotspot_then_staff_home(
        self, _configured, build_payload,
    ):
        self.client.force_login(self.staff_user)
        build_payload.return_value = {
            'login_url': 'https://wifi.test/login',
            'login_origin': 'https://wifi.test',
            'username': 'staff-router-user',
            'password': 'temporary-secret',
            'destination_url': 'https://testserver/staff/',
        }

        response = self.client.post(
            reverse('wifi_entry'),
            {'wifi_action': 'start_staff_wifi'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/hotspot_connect.html')
        self.assertIn('hub_visit', self.client.cookies)
        destination = build_payload.call_args.kwargs['destination_url']
        self.assertEqual(urlsplit(destination).path, reverse('staff_home'))
        self.assertContains(response, 'جارٍ توصيلك بالشبكة')
