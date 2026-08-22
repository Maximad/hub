from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection
from core.models import (
    Category,
    HubVisit,
    InternetEntitlement,
    InternetNetworkOperation,
    InternetPackage,
    InternetSession,
    Order,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings
from internet.models import InternetCatalogBinding


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class InternetMenuCartTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 3')
        self.settings = SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()

        self.food_category = Category.objects.create(name_ar='سناك')
        self.food_section = MenuSection.objects.create(name_ar='سناك')
        self.food = Product.objects.create(
            category=self.food_category,
            name_ar='بطاطا',
            price_syp=200,
            is_available=True,
            product_type=Product.ProductType.FOOD,
            item_type=Product.ItemType.FOOD,
            visible_on_qr=True,
            orderable_on_qr=True,
        )
        self.food.menu_sections.add(self.food_section)

        self.package = InternetPackage.objects.create(
            name_ar='إنترنت سريع — ساعة',
            code='menu-hour',
            price_syp=500,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60,
            visible_to_customer=True,
        )
        self.binding = InternetCatalogBinding.objects.select_related('product').get(package=self.package)
        self.internet_product = self.binding.product
        self.table_url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})

    def tearDown(self):
        get_system_settings.cache_clear()

    def _payload(self, internet_qty='1', include_food=True):
        payload = {
            f'qty_{self.internet_product.pk}': internet_qty,
            'fulfillment_mode': Order.FulfillmentMode.TABLE,
        }
        if include_food:
            payload[f'qty_{self.food.pk}'] = '1'
        return payload

    def _order_from_response(self, response):
        self.assertEqual(response.status_code, 302)
        public_code = response['Location'].rstrip('/').split('/')[-1]
        return Order.objects.get(public_code=public_code)

    def test_table_menu_renders_package_as_an_ordinary_cart_product(self):
        response = self.client.get(self.table_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.package.name_ar)
        self.assertContains(response, f'name="qty_{self.internet_product.pk}"')
        self.assertContains(response, 'إضافة إلى الطلب')
        self.assertContains(response, 'اتصال إنترنت')
        self.assertNotContains(response, 'ابدأ الآن')

    def test_general_menu_does_not_offer_on_premise_internet_product(self):
        response = self.client.get(reverse('menu_public'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.package.name_ar)

    def test_food_and_internet_submit_as_one_order_and_one_visit(self):
        response = self.client.post(self.table_url, self._payload())
        order = self._order_from_response(response)

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(order.table_id, self.table.pk)
        self.assertIsNotNone(order.visit_id)
        self.assertEqual(HubVisit.objects.count(), 1)

        internet_item = order.items.get(product=self.internet_product)
        self.assertEqual(internet_item.quantity, 1)
        self.assertEqual(internet_item.unit_price_syp_snapshot, self.package.price_syp)
        self.assertEqual(internet_item.line_total_syp_snapshot, self.package.price_syp)

        entitlement = InternetEntitlement.objects.get()
        session = InternetSession.objects.get()
        self.assertEqual(entitlement.order_id, order.pk)
        self.assertEqual(entitlement.visit_id, order.visit_id)
        self.assertEqual(entitlement.package_id, self.package.pk)
        self.assertEqual(session.entitlement_id, entitlement.pk)
        self.assertEqual(session.visit_id, order.visit_id)
        self.assertEqual(InternetNetworkOperation.objects.filter(entitlement=entitlement).count(), 1)

    def test_more_than_one_internet_package_quantity_rolls_back_whole_order(self):
        response = self.client.post(self.table_url, self._payload(internet_qty='2'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اختر باقة إنترنت واحدة فقط في كل طلب.')
        self.assertFalse(Order.objects.exists())
        self.assertFalse(HubVisit.objects.exists())
        self.assertFalse(InternetEntitlement.objects.exists())
        self.assertFalse(InternetSession.objects.exists())

    def test_crafted_general_menu_internet_order_is_rejected_atomically(self):
        response = self.client.post(
            reverse('menu_public'),
            {
                f'qty_{self.internet_product.pk}': '1',
                'fulfillment_mode': Order.FulfillmentMode.INSIDE_SPACE,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'باقات الإنترنت متاحة من QR الطاولة')
        self.assertFalse(Order.objects.exists())
        self.assertFalse(HubVisit.objects.exists())
        self.assertFalse(InternetEntitlement.objects.exists())

    def test_package_updates_keep_same_catalog_product_identity(self):
        product_id = self.internet_product.pk
        self.package.name_ar = 'إنترنت سريع — ساعتان'
        self.package.price_syp = 800
        self.package.visible_to_customer = False
        self.package.save(update_fields=['name_ar', 'price_syp', 'visible_to_customer', 'updated_at'])

        binding = InternetCatalogBinding.objects.select_related('product').get(package=self.package)
        self.assertEqual(binding.product_id, product_id)
        self.assertEqual(binding.product.name_ar, self.package.name_ar)
        self.assertEqual(binding.product.price_syp, 800)
        self.assertFalse(binding.product.visible_on_qr)
        self.assertFalse(binding.product.orderable_on_qr)
        self.assertTrue(binding.product.not_discountable)
        self.assertEqual(binding.product.product_type, Product.ProductType.INTERNET)
        self.assertEqual(binding.product.service_type, Product.ServiceType.INTERNET)
