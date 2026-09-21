from unittest.mock import patch
from urllib.parse import urlsplit

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Category,
    HubVisit,
    HubVisitBrowserCredential,
    InternetSession,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings
from locations.models import TableAreaSettings
from internet.models import GuestWifiPolicy, InternetSessionBrowserBinding
from internet.session_network_backends import NOT_PROVISIONED


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

    def enable_wifi_self_service(self, *, require_code=True):
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        policy = GuestWifiPolicy.objects.get(key='default')
        policy.enabled = True
        policy.require_venue_code = require_code
        policy.save(update_fields=['enabled', 'require_venue_code', 'updated_at'])
        return policy

    def test_wifi_entry_is_public_no_store_and_does_not_leak_staff_description(self):
        response = self.client.get(reverse('wifi_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/wifi_entry.html')
        self.assertContains(response, 'أهلاً بك في هَبّ')
        self.assertContains(response, 'css/internet_experience.css')
        self.assertNotContains(response, 'css/staff_workspace.css')
        self.assertContains(response, 'افتح المنيو واطلب')
        self.assertContains(response, 'href="{}"'.format(reverse('menu_public')))
        self.assertContains(response, reverse('wifi_entry') + '?mode=internet')
        self.assertContains(response, 'رقم الطاولة')
        self.assertContains(response, reverse('member_account_login'))
        self.assertNotContains(response, self.access.staff_description)
        self.assertEqual(response['Cache-Control'], 'no-store, private, max-age=0')
        self.assertEqual(response['Pragma'], 'no-cache')
        self.assertIn('noindex', response['X-Robots-Tag'])

    def test_initial_choice_does_not_start_or_render_internet_authorization_form(self):
        self.enable_wifi_self_service(require_code=True)

        response = self.client.get(reverse('wifi_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'عرض خيارات الإنترنت')
        self.assertContains(response, 'افتح المنيو واطلب')
        self.assertNotContains(response, 'name="venue_code"')
        self.assertNotContains(response, 'value="start_guest_wifi"')
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)

    def test_opening_internet_mode_is_read_only_until_customer_starts_access(self):
        self.enable_wifi_self_service(require_code=True)

        response = self.client.get(reverse('wifi_entry'), {'mode': 'internet'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الإنترنت الأساسي')
        self.assertContains(response, 'name="venue_code"')
        self.assertContains(response, 'الرمز خاص بتفعيل الإنترنت الأساسي فقط')
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)

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
        self.assertContains(response, 'href="{}"'.format(table_url + '?view=menu'))
        self.assertNotContains(response, self.access.staff_description)

    def test_free_redirect_marker_is_only_presentational(self):
        response = self.client.get(reverse('wifi_entry'), {'free': '1'})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'تمت العودة من بوابة الشبكة')
        self.assertNotContains(response, 'اتصالك الأساسي جاهز على هذا الجهاز')
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
        self.assertEqual(response['Location'], reverse('current_visit') + '?focus=internet')
        self.assertIn('hub_visit', self.client.cookies)
        visit = HubVisit.objects.get()
        self.assertIsNone(visit.table_id)
        self.assertIsNone(visit.member_id)
        self.assertEqual(visit.notes, 'wifi_internet_options')
        self.assertEqual(HubVisitBrowserCredential.objects.get().visit_id, visit.pk)

        page = self.client.get(response['Location'])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'العضوية ليست شرطاً لشراء الإنترنت السريع')
        content = page.content.decode()
        self.assertLess(content.index('id="internet"'), content.index('aria-label="الحساب"'))

    @patch('core.views.visits.build_session_hotspot_login_payload')
    @patch('core.views.wifi.one_tap_session_connect_configured', return_value=True)
    @patch('core.views.wifi.prepare_guest_wifi_session_network', return_value=True)
    def test_one_tap_basic_connection_opens_menu_after_router_login(
        self, _prepare, _one_tap, build_payload,
    ):
        self.enable_wifi_self_service(require_code=False)
        build_payload.return_value = {
            'login_url': 'https://wifi.test/login',
            'login_origin': 'https://wifi.test',
            'username': 'temporary-user',
            'password': 'temporary-secret',
            'destination_url': 'https://testserver/menu/',
        }

        response = self.client.post(
            reverse('wifi_entry'), {'wifi_action': 'start_guest_wifi'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/hotspot_connect.html')
        self.assertIn('hub_visit', self.client.cookies)
        destination = build_payload.call_args.kwargs['destination_url']
        self.assertEqual(urlsplit(destination).path, reverse('menu_public'))
        self.assertContains(response, 'لم يتم الاتصال؟ أعد المحاولة')

    @patch('core.views.wifi.prepare_guest_wifi_session_network', return_value=False)
    def test_failed_basic_network_authorization_is_not_presented_as_connected(self, _prepare):
        self.enable_wifi_self_service(require_code=False)

        started = self.client.post(reverse('wifi_entry'), {'wifi_action': 'start_guest_wifi'})

        self.assertEqual(started.status_code, 302)
        self.assertEqual(started['Location'], reverse('wifi_entry') + '?mode=internet')
        page = self.client.get(started['Location'])
        self.assertContains(page, 'قيد التجهيز')
        self.assertContains(page, 'الشبكة لم تؤكد الجاهزية بعد')
        self.assertNotContains(page, 'أكدت الشبكة تجهيز الاتصال')

    def test_pending_fast_session_is_not_presented_as_connected(self):
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        opened = self.client.post(reverse('wifi_entry'), {'wifi_action': 'internet_options'})
        self.assertEqual(opened.status_code, 302)
        visit = HubVisit.objects.get()
        credential = HubVisitBrowserCredential.objects.get(visit=visit)
        now = timezone.now()
        session = InternetSession.objects.create(
            visit=visit,
            started_at=now,
            start_time=now,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
            network_status=NOT_PROVISIONED,
        )
        InternetSessionBrowserBinding.objects.create(session=session, credential=credential)

        response = self.client.get(reverse('wifi_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'طلب الإنترنت السريع قيد التجهيز')
        self.assertContains(response, 'لا تعرض الصفحة هذا كاتصال فعلي قبل التأكيد')
        self.assertNotContains(response, 'الإنترنت السريع جاهز لهذا الجهاز')

    def test_menu_remains_available_without_pin_or_starting_internet(self):
        self.enable_wifi_self_service(require_code=True)
        landing = self.client.get(reverse('wifi_entry'))
        self.assertContains(landing, 'href="{}"'.format(reverse('menu_public')))
        self.assertContains(landing, 'لا يلزم رمز المكان')

        menu = self.client.get(reverse('menu_public'))

        self.assertEqual(menu.status_code, 200)
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)

    def test_stopping_fast_restores_remaining_basic_allowance(self):
        category = Category.objects.create(name_ar='خدمات')
        internet_product = Product.objects.create(
            category=category,
            name_ar='إنترنت حسب الوقت',
            price_syp=0,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
            requires_preparation=False,
            visible_on_qr=False,
            orderable_on_qr=False,
            visible_on_pos=False,
            orderable_on_pos=False,
            not_discountable=True,
            track_margin=False,
        )
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
            internet_metered_enabled=True,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=1,
            default_rounding_increment_minutes=1,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=internet_product,
        )
        get_system_settings.cache_clear()
        policy = GuestWifiPolicy.objects.get(key='default')
        policy.enabled = True
        policy.require_venue_code = False
        policy.save(update_fields=['enabled', 'require_venue_code', 'updated_at'])

        basic_start = self.client.post(reverse('wifi_entry'), {
            'wifi_action': 'start_guest_wifi',
        })
        self.assertEqual(basic_start.status_code, 302)
        first_basic = InternetSession.objects.get(billing_mode=InternetSession.BillingMode.FREE)

        fast_start = self.client.post(reverse('visit_internet_start'), {'mode': 'metered'})
        self.assertEqual(fast_start.status_code, 302)
        first_basic.refresh_from_db()
        self.assertEqual(first_basic.status, InternetSession.Status.ENDED)
        fast = InternetSession.objects.get(billing_mode=InternetSession.BillingMode.OPEN_METERED)

        stopped = self.client.post(reverse(
            'visit_internet_session_stop', kwargs={'public_code': fast.public_code},
        ))

        self.assertEqual(stopped.status_code, 302)
        self.assertEqual(stopped['Location'], reverse('wifi_entry'))
        fast.refresh_from_db()
        self.assertEqual(fast.status, InternetSession.Status.BILLED)
        active = InternetSession.objects.get(status=InternetSession.Status.ACTIVE)
        self.assertEqual(active.billing_mode, InternetSession.BillingMode.FREE)
        self.assertNotEqual(active.pk, first_basic.pk)
