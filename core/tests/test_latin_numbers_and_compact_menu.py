from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings

from catalog.models import MenuSection, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.models import Category, Order, OrderItem, Product, SystemSetting
from core.settings_helpers import get_system_settings
from core.templatetags.hub_numbers import format_syp, latin_digits


class LatinNumberFormattingTests(TestCase):
    def test_format_syp_uses_latin_digits_and_separators(self):
        self.assertEqual(format_syp(25000), '25,000 ل.س')

    def test_latin_digits_converts_arabic_indic_input(self):
        self.assertEqual(latin_digits('٢٥,٠٠٠ ل.س'), '25,000 ل.س')

    def test_latin_digits_converts_persian_input(self):
        self.assertEqual(latin_digits('۲۵,۰۰۰ ل.س'), '25,000 ل.س')

    def test_blank_values_do_not_crash(self):
        self.assertEqual(latin_digits(None), '')
        self.assertEqual(format_syp(''), '')


class CompactMenuRenderingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='numbers-admin', password='pass', email='numbers@example.com', phone='+963900000003'
        )
        self.category = Category.objects.create(name_ar='مشروبات')
        self.section = MenuSection.objects.create(name_ar='القهوة')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='لاتيه',
            description_ar='وصف قصير للقهوة',
            price_syp=25000,
            visible_on_qr=True,
            orderable_on_qr=True,
        )
        self.product.menu_sections.add(self.section)
        self.group = ProductOptionGroup.objects.create(code='size', name_ar='الحجم', is_required=True, min_selected=1)
        ProductOption.objects.create(group=self.group, code='large', name_ar='كبير', price_delta_syp=5000)
        ProductOptionGroupAssignment.objects.create(product=self.product, group=self.group)

    def tearDown(self):
        get_system_settings.cache_clear()

    def _settings(self, **kwargs):
        get_system_settings.cache_clear()
        setting = SystemSetting.objects.create(system_title_ar='اختبار الأرقام', **kwargs)
        get_system_settings.cache_clear()
        return setting


    def test_global_numeric_assets_are_loaded_from_base_template(self):
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'js/hub_numbers.js')

    def test_global_numeric_css_covers_raw_tables_badges_and_inputs(self):
        css_path = finders.find('css/hub.css')
        self.assertIsNotNone(css_path)
        css = open(css_path, encoding='utf-8').read()
        for selector in ['.hub-money', '.hub-order-number', 'input[type="number"]', 'input[inputmode="numeric"]', 'input[type="tel"]', '.hub-table td', '.hub-badge']:
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
        self.assertIn('unicode-bidi:isolate', css)
        self.assertIn('font-variant-numeric:tabular-nums', css)

    def test_global_numeric_script_normalizes_text_nodes_and_inputs(self):
        js_path = finders.find('js/hub_numbers.js')
        self.assertIsNotNone(js_path)
        js = open(js_path, encoding='utf-8').read()
        self.assertIn('window.HubNumbers', js)
        self.assertIn('input[type="number"], input[inputmode="numeric"], input[type="tel"]', js)
        self.assertIn('.hub-table td', js)
        self.assertIn('MutationObserver', js)

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_public_menu_compact_list_uses_clickable_rows_and_modal_controls(self):
        self._settings(public_menu_layout=SystemSetting.PublicMenuLayout.COMPACT_LIST, show_item_notes=True)
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'menu-layout-compact_list')
        self.assertContains(response, '25,000 ل.س')
        self.assertContains(response, 'js/hub_numbers.js')
        self.assertNotContains(response, '٢٥')
        self.assertContains(response, 'data-menu-item-open')
        self.assertContains(response, 'role="button"')
        self.assertContains(response, 'data-menu-modal')
        self.assertContains(response, 'menu-item-modal-source')
        self.assertContains(response, 'name="note_')
        self.assertContains(response, 'خيارات متاحة')
        self.assertContains(response, 'menu-product-media--placeholder')
        self.assertNotContains(response, 'menu-list-actions')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_invalid_public_menu_layout_falls_back(self):
        setting = self._settings()
        SystemSetting.objects.filter(pk=setting.pk).update(public_menu_layout='bad')
        get_system_settings.cache_clear()
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'public-menu-layout-comfortable')


    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_public_menu_hides_delivery_rows_when_delivery_disabled(self):
        self._settings(delivery_enabled=False, enable_delivery=False)
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-delivery-fee-row')
        self.assertNotContains(response, 'الإجمالي مع التوصيل')
        self.assertNotContains(response, 'data-delivery-fields')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_public_menu_renders_delivery_rows_when_delivery_enabled(self):
        self._settings(delivery_enabled=True, delivery_fee_mode=SystemSetting.DeliveryFeeMode.FIXED, fixed_delivery_fee_syp=3000)
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-delivery-fee-row')
        self.assertContains(response, 'رسوم التوصيل')
        self.assertContains(response, '3,000 ل.س')
        self.assertNotContains(response, '٣,٠٠٠')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_order_confirmation_delivery_fee_is_delivery_only_and_latin_digits(self):
        order = Order.objects.create(fulfillment_mode=Order.FulfillmentMode.INSIDE_SPACE, service_mode=Order.ServiceMode.DINE_IN, subtotal_syp=25000, delivery_fee_syp=3000)
        OrderItem.objects.create(order=order, product=self.product, product_name_ar_snapshot=self.product.name_ar, quantity=1, unit_price_syp_snapshot=25000)
        response = self.client.get(f'/order/{order.public_code}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '25,000 ل.س')
        self.assertNotContains(response, 'رسوم التوصيل')
        self.assertNotContains(response, '٢٥')

        delivery_order = Order.objects.create(fulfillment_mode=Order.FulfillmentMode.DELIVERY, service_mode=Order.ServiceMode.DINE_IN, subtotal_syp=25000, delivery_fee_syp=3000)
        OrderItem.objects.create(order=delivery_order, product=self.product, product_name_ar_snapshot=self.product.name_ar, quantity=1, unit_price_syp_snapshot=25000)
        delivery_response = self.client.get(f'/order/{delivery_order.public_code}/')
        self.assertEqual(delivery_response.status_code, 200)
        self.assertContains(delivery_response, 'رسوم التوصيل')
        self.assertContains(delivery_response, '28,000 ل.س')
        self.assertContains(delivery_response, 'js/hub_numbers.js')
        self.assertNotContains(delivery_response, '٢٨')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_staff_pos_contains_latin_digit_prices_and_core_staff_pages_load(self):
        Order.objects.create(fulfillment_mode=Order.FulfillmentMode.INSIDE_SPACE, service_mode=Order.ServiceMode.DINE_IN, subtotal_syp=25000)
        self.client.force_login(self.user)
        for path in ['/staff/pos/', '/staff/orders/', '/staff/cashier/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertLess(response.status_code, 500)
        response = self.client.get('/staff/pos/')
        self.assertContains(response, '25,000 ل.س')
        self.assertContains(response, 'js/hub_numbers.js')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_staff_raw_order_board_values_are_covered_by_global_layer(self):
        order = Order.objects.create(fulfillment_mode=Order.FulfillmentMode.INSIDE_SPACE, service_mode=Order.ServiceMode.DINE_IN, subtotal_syp=25000)
        OrderItem.objects.create(order=order, product=self.product, product_name_ar_snapshot=self.product.name_ar, quantity=2, unit_price_syp_snapshot=25000)
        self.client.force_login(self.user)
        response = self.client.get('/staff/orders/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'رقم الطلب:')
        self.assertContains(response, order.display_number)
        self.assertContains(response, 'js/hub_numbers.js')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_input_rendering_case_loads_global_normalizer(self):
        self.client.force_login(self.user)
        response = self.client.get('/staff/reservations/new/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="number"')
        self.assertContains(response, 'js/hub_numbers.js')
