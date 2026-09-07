from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import HubVisit, HubVisitBrowserCredential, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings
from locations.models import TableAreaSettings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class WifiEntryTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='مدور 2')
        self.access = TableAreaSettings.objects.create(
            table=self.table,
            customer_entry_code='11',
            staff_description='INTERNAL STAFF DESCRIPTION — NEVER PUBLIC',
        )

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_wifi_entry_is_public_no_store_and_does_not_leak_staff_description(self):
        response = self.client.get(reverse('wifi_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/wifi_entry.html')
        self.assertContains(response, 'أهلاً بك في هَبّ')
        self.assertContains(response, 'رقم الطاولة')
        self.assertContains(response, reverse('member_account_login'))
        self.assertNotContains(response, self.access.staff_description)
        self.assertEqual(response['Cache-Control'], 'no-store, private, max-age=0')
        self.assertEqual(response['Pragma'], 'no-cache')
        self.assertIn('noindex', response['X-Robots-Tag'])

    def test_wifi_entry_resolves_explicit_table_number_with_arabic_digits(self):
        response = self.client.get(reverse('wifi_entry'), {'table_number': '١١'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('menu_table', kwargs={'qr_token': self.table.qr_token}),
        )

    def test_wifi_entry_does_not_infer_number_from_table_name(self):
        other = TableArea.objects.create(room=self.room, name_ar='طاولة اسمها 77')
        TableAreaSettings.objects.create(table=other, customer_entry_code='12')

        response = self.client.get(reverse('wifi_entry'), {'table_number': '77'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'رقم الطاولة غير موجود')

    def test_wifi_entry_shows_current_bound_table_without_staff_description(self):
        SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()
        table_url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})
        created = self.client.post(table_url, {'visit_action': 'create'})
        self.assertEqual(created.status_code, 302)

        response = self.client.get(reverse('wifi_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'حسابك الحالي')
        self.assertContains(response, self.table.name_ar)
        self.assertContains(response, table_url + '?view=menu')
        self.assertNotContains(response, self.access.staff_description)

    def test_free_redirect_marker_is_only_presentational(self):
        response = self.client.get(reverse('wifi_entry'), {'free': '1'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اتصالك الأساسي جاهز على هذا الجهاز')
        self.assertNotContains(response, 'mac-address')
        self.assertNotContains(response, 'username')
        self.assertNotContains(response, 'password')

    def test_fast_internet_options_create_tableless_visit_without_membership(self):
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()

        response = self.client.post(reverse('wifi_entry'), {'wifi_action': 'internet_options'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('current_visit'))
        self.assertIn('hub_visit', self.client.cookies)
        visit = HubVisit.objects.get()
        self.assertIsNone(visit.table_id)
        self.assertIsNone(visit.member_id)
        self.assertEqual(visit.notes, 'wifi_internet_options')
        self.assertEqual(HubVisitBrowserCredential.objects.get().visit_id, visit.pk)

        page = self.client.get(reverse('current_visit'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'العضوية ليست شرطاً لشراء الإنترنت السريع')
