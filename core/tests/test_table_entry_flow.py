from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection
from core.models import (
    Category,
    HubVisit,
    HubVisitBrowserCredential,
    InternetEntitlement,
    InternetPackage,
    InternetSession,
    Order,
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
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()

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

    def test_table_qr_opens_simple_entry_screen(self):
        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/table_landing.html')
        self.assertContains(response, 'الإنترنت')
        self.assertContains(response, 'المنيو')
        self.assertContains(response, self.package.name_ar)
        self.assertContains(response, 'name="next" value="menu"')
        self.assertNotContains(response, self.food.name_ar)
        self.assertNotContains(response, '<details class="table-entry__choice table-entry__choice--internet" open', html=False)

    def test_menu_choice_opens_catalog_without_generic_internet_product(self):
        response = self.client.get(self.menu_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/menu.html')
        self.assertContains(response, self.food.name_ar)
        self.assertNotContains(response, self.package.name_ar)

    def test_internet_quick_start_creates_visit_sale_session_then_redirects_to_menu(self):
        response = self.client.post(
            reverse('visit_internet_start'),
            {
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

    def test_entry_screen_exposes_session_link_after_visit_is_bound(self):
        first = self.client.post(
            reverse('visit_internet_start'),
            {
                'package': str(self.package.public_code),
                'table': str(self.table.qr_token),
                'request_key': 'table-entry-session-link',
                'next': 'menu',
            },
        )
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value

        response = self.client.get(self.entry_url)

        self.assertContains(response, 'جلستك')
        self.assertContains(response, 'الإنترنت فعال الآن')
        self.assertContains(response, reverse('current_visit'))

    def test_current_visit_returns_directly_to_catalog(self):
        first = self.client.post(
            reverse('visit_internet_start'),
            {
                'package': str(self.package.public_code),
                'table': str(self.table.qr_token),
                'request_key': 'table-entry-current-visit',
            },
        )
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value

        response = self.client.get(reverse('current_visit'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.menu_url)
