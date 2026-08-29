from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection
from core.models import Category, HubVisit, Order, Product, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class CustomerSpaceTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 3')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.section = MenuSection.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='شاي',
            price_syp=100,
            is_available=True,
            visible_on_qr=True,
            orderable_on_qr=True,
        )
        self.product.menu_sections.add(self.section)
        self.settings = SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()
        self.menu_url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})
        self.catalog_url = self.menu_url

    def tearDown(self):
        get_system_settings.cache_clear()

    def _bind_visit(self):
        if 'hub_visit' not in self.client.cookies:
            response = self.client.post(self.menu_url, {'visit_action': 'create'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Location'], self.menu_url)
            self.assertIn('hub_visit', self.client.cookies)
        return HubVisit.objects.order_by('-pk').first()

    def _start_visit_with_order(self):
        self._bind_visit()
        response = self.client.post(
            self.menu_url,
            {
                f'qty_{self.product.pk}': '1',
                'fulfillment_mode': Order.FulfillmentMode.TABLE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.menu_url)
        return response

    def test_public_menu_loads_customer_space_assets(self):
        self._bind_visit()
        response = self.client.get(self.catalog_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'public-menu-page')
        self.assertContains(response, 'css/customer_space.css')
        self.assertContains(response, 'js/customer_space.js')

    def test_current_visit_is_customer_session_screen(self):
        self._start_visit_with_order()
        response = self.client.get(reverse('current_visit'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'customer-space-page')
        self.assertContains(response, 'data-customer-space')
        self.assertContains(response, 'data-has-visit="true"')
        self.assertContains(response, 'id="customer-orders"')
        self.assertContains(response, 'طلباتي')
        self.assertContains(response, self.table.display_name)
        self.assertContains(response, '+ أضف طلباً')
        self.assertContains(response, self.menu_url)
        self.assertContains(response, 'المنيو')
        self.assertContains(response, 'جلستي')

    def test_visit_order_submission_returns_to_menu_not_confirmation_screen(self):
        order_response = self._start_visit_with_order()
        menu = self.client.get(order_response['Location'])

        self.assertEqual(menu.status_code, 200)
        self.assertContains(menu, 'public-menu-page')
        self.assertContains(menu, 'تم إرسال الطلب')
        self.assertContains(menu, reverse('current_visit'))
        self.assertNotContains(menu, 'customer-order-confirm-page')
        self.assertNotContains(menu, 'احتفظ برمز QR')
        self.assertEqual(Order.objects.count(), 1)

    def test_visit_and_order_still_share_same_operational_record(self):
        self._start_visit_with_order()
        visit = HubVisit.objects.get()
        order = Order.objects.get()
        self.assertEqual(order.visit_id, visit.pk)
        self.assertEqual(order.table_id, self.table.pk)
