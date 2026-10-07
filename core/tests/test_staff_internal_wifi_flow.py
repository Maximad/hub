from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
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
from core.services.visits import issue_visit_credential


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

    @patch('core.views.wifi._entitlement_hotspot_relay_response')
    @patch('core.views.wifi.one_tap_connect_configured', return_value=True)
    def test_non_admin_staff_can_sign_in_and_connect_without_customer_visit(
        self, _configured, relay,
    ):
        self.assertFalse(self.staff_user.is_staff)
        relay.return_value = HttpResponseRedirect(reverse('staff_home'))

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
        self.assertEqual(signed_in['Location'], reverse('staff_home'))
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_authenticated_staff_sees_private_grant_as_primary_wifi_option(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse('wifi_entry'), {'mode': 'internet'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اتصل بإنترنت الفريق')
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

    @patch('core.views.wifi._entitlement_hotspot_relay_response')
    @patch('core.views.wifi.one_tap_connect_configured', return_value=True)
    def test_staff_wifi_start_never_creates_customer_visit_or_customer_session(
        self, _configured, relay,
    ):
        self.client.force_login(self.staff_user)
        relay.return_value = HttpResponseRedirect(reverse('staff_home'))

        response = self.client.post(
            reverse('wifi_entry'),
            {'wifi_action': 'start_staff_wifi'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('staff_home'))
        self.assertNotIn('hub_visit', response.cookies)
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        relay.assert_called_once()
        self.assertEqual(relay.call_args.args[1].pk, self.entitlement.pk)

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

    @patch('core.views.wifi._entitlement_hotspot_relay_response')
    @patch('core.views.wifi.one_tap_connect_configured', return_value=True)
    def test_reconnect_retires_legacy_synthetic_staff_visit(
        self, _configured, relay,
    ):
        self.client.force_login(self.staff_user)
        visit = HubVisit.objects.create(notes='staff_internal_wifi')
        credential, raw = issue_visit_credential(visit)
        self.client.cookies['hub_visit'] = raw
        session = InternetSession.objects.create(
            entitlement=self.entitlement,
            visit=visit,
            status=InternetSession.Status.ACTIVE,
            billing_mode=InternetSession.BillingMode.PREPAID,
            started_by=self.staff_user,
        )
        relay.return_value = HttpResponseRedirect(reverse('staff_home'))

        response = self.client.post(
            reverse('wifi_entry'),
            {'wifi_action': 'start_staff_wifi'},
        )

        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        session.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.CLOSED)
        self.assertEqual(session.status, InternetSession.Status.ENDED)
        self.assertIsNotNone(credential.revoked_at)

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
        self.assertNotIn('hub_visit', response.cookies)
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)
        destination = build_payload.call_args.kwargs['destination_url']
        self.assertEqual(urlsplit(destination).path, reverse('staff_home'))
        self.assertContains(response, 'جارٍ توصيلك بالشبكة')
