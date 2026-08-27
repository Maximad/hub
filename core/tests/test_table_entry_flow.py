from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import MenuSection
from core.models import (
    Category,
    HubVisit,
    HubVisitBrowserCredential,
    InternetEntitlement,
    InternetPackage,
    InternetSession,
    Order,
    Payment,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class TableEntryFlowTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 7')

        category = Category.objects.create(name_ar='سناك')
        section = MenuSection.objects.create(name_ar='سناك', is_active=True, visible_on_qr=True)
        self.food = Product.objects.create(
            category=category,
            name_ar='ساندويش',
            price_syp=300,
            is_available=True,
            product_type=Product.ProductType.FOOD,
            item_type=Product.ItemType.FOOD,
            visible_on_qr=True,
            orderable_on_qr=True,
        )
        self.food.menu_sections.add(section)
        self.internet_service_product = Product.objects.create(
            category=category,
            name_ar='إنترنت حسب الوقت',
            price_syp=0,
            is_available=True,
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
        self.settings = SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
            internet_metered_enabled=True,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=30,
            default_rounding_increment_minutes=15,
            default_minimum_charge_syp=0,
            default_free_grace_minutes=0,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=self.internet_service_product,
        )
        get_system_settings.cache_clear()

        self.package = InternetPackage.objects.create(
            name_ar='ساعة إنترنت',
            code='entry-hour',
            price_syp=500,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60,
            visible_to_customer=True,
        )
        self.entry_url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})
        self.menu_url = self.entry_url + '?view=menu'

    def tearDown(self):
        get_system_settings.cache_clear()

    def _start_metered(self):
        return self.client.post(
            reverse('visit_internet_start'),
            {
                'mode': 'metered',
                'table': str(self.table.qr_token),
                'next': 'menu',
            },
        )

    def test_table_qr_opens_simple_entry_screen_with_metered_default(self):
        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/table_landing.html')
        self.assertContains(response, 'الإنترنت السريع')
        self.assertContains(response, 'اتصل بالإنترنت السريع')
        self.assertContains(response, 'اختر باقة')
        self.assertContains(response, '600 ل.س')
        self.assertContains(response, self.package.name_ar)
        self.assertContains(response, 'name="mode" value="metered"')
        self.assertContains(response, 'name="mode" value="package"')
        self.assertContains(response, 'name="next" value="menu"')
        self.assertNotContains(response, self.food.name_ar)
        self.assertNotContains(
            response,
            '<details class="table-entry__choice table-entry__choice--internet"',
            html=False,
        )
        self.assertContains(response, 'class="table-entry__package-picker"')

    def test_menu_choice_opens_catalog_without_generic_internet_product(self):
        response = self.client.get(self.menu_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/menu.html')
        self.assertContains(response, self.food.name_ar)
        self.assertNotContains(response, self.package.name_ar)
        self.assertNotContains(response, self.internet_service_product.name_ar)

    def test_metered_quick_start_creates_visit_session_without_package_charge_yet(self):
        response = self._start_metered()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.entry_url + '?view=menu&internet_started=1')
        self.assertIn('hub_visit', response.cookies)
        visit = HubVisit.objects.get()
        session = InternetSession.objects.get()
        self.assertEqual(visit.table_id, self.table.pk)
        self.assertEqual(session.visit_id, visit.pk)
        self.assertIsNone(session.package_id)
        self.assertIsNone(session.entitlement_id)
        self.assertEqual(session.billing_mode, InternetSession.BillingMode.OPEN_METERED)
        self.assertEqual(session.rate_per_hour_syp, 600)
        self.assertEqual(session.minimum_minutes, 30)
        self.assertEqual(session.rounding_increment_minutes, 15)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(InternetEntitlement.objects.count(), 0)
        self.assertEqual(HubVisitBrowserCredential.objects.get().visit_id, visit.pk)

    def test_repeated_metered_start_reuses_current_direct_session(self):
        first = self._start_metered()
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value

        second = self._start_metered()

        self.assertEqual(second.status_code, 302)
        self.assertEqual(InternetSession.objects.count(), 1)
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 0)

    def test_package_quick_start_still_creates_visit_sale_session_then_redirects_to_menu(self):
        response = self.client.post(
            reverse('visit_internet_start'),
            {
                'mode': 'package',
                'package': str(self.package.public_code),
                'table': str(self.table.qr_token),
                'request_key': 'table-entry-quick-start',
                'next': 'menu',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.entry_url + '?view=menu&internet_started=1')
        self.assertIn('hub_visit', response.cookies)
        visit = HubVisit.objects.get()
        entitlement = InternetEntitlement.objects.get()
        session = InternetSession.objects.get()
        order = Order.objects.get()
        self.assertEqual(visit.table_id, self.table.pk)
        self.assertEqual(entitlement.visit_id, visit.pk)
        self.assertEqual(entitlement.order_id, order.pk)
        self.assertEqual(session.visit_id, visit.pk)
        self.assertEqual(session.entitlement_id, entitlement.pk)
        self.assertEqual(order.visit_id, visit.pk)
        self.assertEqual(order.remaining_syp, self.package.price_syp)
        self.assertEqual(HubVisitBrowserCredential.objects.get().visit_id, visit.pk)

    def test_entry_screen_exposes_metered_session_link_after_visit_is_bound(self):
        first = self._start_metered()
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value

        response = self.client.get(self.entry_url)

        self.assertContains(response, 'جلستك')
        self.assertContains(response, 'الإنترنت السريع فعال الآن')
        self.assertContains(response, 'جلسة حسب الوقت')
        self.assertContains(response, 'إدارة جلستك الحالية')
        self.assertNotContains(response, 'اتصل بالإنترنت السريع')
        self.assertContains(response, reverse('current_visit'))

    def test_package_is_rejected_while_metered_visit_session_is_active(self):
        first = self._start_metered()
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value
        second_package = InternetPackage.objects.create(
            name_ar='ثلاث ساعات',
            code='entry-three-hours',
            price_syp=900,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=180,
            visible_to_customer=True,
        )

        response = self.client.post(
            reverse('visit_internet_start'),
            {
                'mode': 'package',
                'package': str(second_package.public_code),
                'table': str(self.table.qr_token),
                'request_key': 'table-entry-second-session',
                'next': 'menu',
            },
            HTTP_REFERER=self.entry_url,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.entry_url)
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(InternetEntitlement.objects.count(), 0)
        self.assertEqual(InternetSession.objects.filter(status=InternetSession.Status.ACTIVE).count(), 1)

    def test_customer_stop_bills_metered_session_into_same_visit(self):
        first = self._start_metered()
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value
        session = InternetSession.objects.get()
        started = timezone.now() - timedelta(minutes=61)
        InternetSession.objects.filter(pk=session.pk).update(start_time=started, started_at=started)
        session.refresh_from_db()

        response = self.client.post(
            reverse('visit_internet_session_stop', kwargs={'public_code': session.public_code})
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('current_visit'))
        session.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.BILLED)
        self.assertEqual(session.billable_minutes, 75)
        self.assertEqual(session.payable_total_syp, 750)
        order = Order.objects.get()
        self.assertEqual(order.visit_id, session.visit_id)
        self.assertEqual(order.table_id, self.table.pk)
        self.assertEqual(order.total_syp, 750)
        item = order.items.get()
        self.assertEqual(item.product_id, self.internet_service_product.pk)
        self.assertEqual(item.line_total_syp_snapshot, 750)

    def test_staff_close_stops_and_bills_metered_session_before_balance_check(self):
        first = self._start_metered()
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value
        visit = HubVisit.objects.get()
        session = InternetSession.objects.get()
        started = timezone.now() - timedelta(minutes=31)
        InternetSession.objects.filter(pk=session.pk).update(start_time=started, started_at=started)

        user = get_user_model().objects.create_superuser(
            username='table-metered-admin',
            password='pass',
            email='metered@example.com',
            phone='+963900009999',
        )
        self.client.force_login(user)
        close_url = reverse('staff_visit_detail', kwargs={'public_code': visit.public_code})

        response = self.client.post(close_url, {'action': 'close'})

        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.OPEN)
        self.assertEqual(session.status, InternetSession.Status.BILLED)
        order = Order.objects.get()
        self.assertEqual(order.visit_id, visit.pk)
        self.assertGreater(order.remaining_syp, 0)

        Payment.objects.create(
            order=order,
            amount_syp=order.remaining_syp,
            method=Payment.Method.CASH,
            created_by=user,
        )
        self.client.post(close_url, {'action': 'close'})
        visit.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.CLOSED)

    def test_current_visit_returns_directly_to_catalog_and_describes_metered_session(self):
        first = self._start_metered()
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value

        response = self.client.get(reverse('current_visit'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.menu_url)
        self.assertContains(response, 'جلسة إنترنت حسب الوقت')
        self.assertContains(response, '600 ل.س')
