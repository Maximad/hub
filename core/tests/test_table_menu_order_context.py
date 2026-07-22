import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.models import Category, Order, OrderItem, Product, Room, TableArea


@override_settings(DEBUG_PROPAGATE_EXCEPTIONS=False, ALLOWED_HOSTS=['testserver'], STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage', STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TableMenuOrderContextTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='table-menu-admin', password='pass', email='table-menu@example.com', phone='+963900000777'
        )
        self.room = Room.objects.create(name_ar='الصالة العربية', name_en='Arabic Hall')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 7', name_en='Table 7')
        self.other_room = Room.objects.create(name_ar='الغرفة الأخرى')
        self.other_table = TableArea.objects.create(room=self.other_room, name_ar='طاولة مزيفة')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.section = MenuSection.objects.create(name_ar='القهوة')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='لاتيه',
            name_en='Latte',
            price_syp=25000,
            is_available=True,
            visible_on_qr=True,
            orderable_on_qr=True,
        )
        self.product.menu_sections.add(self.section)
        self.option_group = ProductOptionGroup.objects.create(
            code='milk',
            name_ar='الحليب',
            selection_type=ProductOptionGroup.SelectionType.SINGLE,
            is_required=True,
            min_selected=1,
            max_selected=1,
        )
        self.option = ProductOption.objects.create(
            group=self.option_group,
            code='oat',
            name_ar='شوفان',
            price_delta_syp=3000,
        )
        ProductOptionGroupAssignment.objects.create(product=self.product, group=self.option_group)

    def _post_payload(self, **extra):
        payload = {
            f'qty_{self.product.id}': '2',
            f'note_{self.product.id}': 'سكر خفيف',
            f'option_{self.product.id}_{self.option_group.id}': str(self.option.id),
            'fulfillment_mode': Order.FulfillmentMode.INSIDE_SPACE,
            'customer_name': 'زبون',
            'customer_phone': '+963 900 000 000',
            'general_note': 'ملاحظة عامة',
        }
        payload.update(extra)
        return payload

    def _created_order_from_response(self, response):
        self.assertEqual(response.status_code, 302)
        public_code = response['Location'].rstrip('/').split('/')[-1]
        return Order.objects.select_related('table', 'table__room').get(public_code=public_code)

    def test_public_menu_returns_200_and_uses_normal_controls(self):
        response = self.client.get(reverse('menu_public'))
        self.assertEqual(response.status_code, 200)
        for expected in [
            'id="menu-order-form"',
            'data-product-card',
            'data-cart-action="increment"',
            'data-cart-action="decrement"',
            'data-add-to-cart',
            'name="qty_',
            'name="note_',
            'data-option-input',
            'id="cart-review"',
            'js/hub_cart_common.js',
            'js/menu_cart.js',
        ]:
            with self.subTest(expected=expected):
                self.assertContains(response, expected)

    def test_table_menu_returns_200_uses_same_controls_and_displays_context(self):
        response = self.client.get(reverse('menu_table', kwargs={'qr_token': self.table.qr_token}))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'menu/menu.html')
        for expected in [
            'id="menu-order-form"',
            'data-product-card',
            'data-cart-action="increment"',
            'data-cart-action="decrement"',
            'data-add-to-cart',
            'name="qty_',
            'name="note_',
            'data-option-input',
            'id="cart-review"',
            'js/hub_cart_common.js',
            'js/menu_cart.js',
            self.table.name_ar,
            self.room.name_ar,
        ]:
            with self.subTest(expected=expected):
                self.assertContains(response, expected)

    def test_invalid_qr_token_is_rejected_safely(self):
        response = self.client.get(reverse('menu_table', kwargs={'qr_token': uuid.uuid4()}))
        self.assertEqual(response.status_code, 404)

    def test_normal_menu_order_has_no_table_and_no_table_leakage(self):
        table_response = self.client.post(
            reverse('menu_table', kwargs={'qr_token': self.table.qr_token}),
            self._post_payload(fulfillment_mode=Order.FulfillmentMode.TABLE),
        )
        table_order = self._created_order_from_response(table_response)
        self.assertEqual(table_order.table, self.table)

        general_response = self.client.post(reverse('menu_public'), self._post_payload())
        general_order = self._created_order_from_response(general_response)
        self.assertIsNone(general_order.table)
        self.assertEqual(general_order.fulfillment_mode, Order.FulfillmentMode.INSIDE_SPACE)

    def test_table_menu_order_uses_url_table_and_existing_workflow(self):
        response = self.client.post(
            reverse('menu_table', kwargs={'qr_token': self.table.qr_token}),
            self._post_payload(
                fulfillment_mode=Order.FulfillmentMode.TABLE,
                table_id=str(self.other_table.id),
                table_name=self.other_table.name_ar,
                total_syp='1',
                price_syp='1',
            ),
        )
        order = self._created_order_from_response(response)
        self.assertEqual(order.table, self.table)
        self.assertEqual(order.table.room, self.room)
        self.assertEqual(order.fulfillment_mode, Order.FulfillmentMode.TABLE)
        self.assertEqual(order.service_mode, Order.ServiceMode.TABLE)
        self.assertFalse(order.is_delivery)
        self.assertIn(self.table.name_ar, order.get_fulfillment_label())
        self.assertIn(self.room.name_ar, order.location_detail)

        item = order.items.get(product=self.product)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.item_note, 'سكر خفيف')
        self.assertEqual(item.unit_price_syp_snapshot, 28000)
        self.assertEqual(item.line_total_syp_snapshot, 56000)
        self.assertEqual(item.selected_options_snapshot[0]['options'][0]['option_id'], self.option.id)

    def test_table_information_appears_on_confirmation_staff_orders_and_cashier(self):
        order = self._created_order_from_response(
            self.client.post(
                reverse('menu_table', kwargs={'qr_token': self.table.qr_token}),
                self._post_payload(fulfillment_mode=Order.FulfillmentMode.TABLE),
            )
        )
        confirm_response = self.client.get(reverse('order_public', kwargs={'public_code': order.public_code}))
        self.assertContains(confirm_response, self.table.name_ar)
        self.assertContains(confirm_response, self.room.name_ar)

        self.client.force_login(self.user)
        for route_name, kwargs in [
            ('staff_orders', {}),
            ('staff_cashier', {}),
            ('staff_cashier_order', {'public_code': order.public_code}),
            ('staff_order_edit', {'public_code': order.public_code}),
        ]:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, self.table.name_ar)
                self.assertContains(response, self.room.name_ar)

    def test_modifier_validation_still_rejects_missing_required_modifier(self):
        response = self.client.post(
            reverse('menu_table', kwargs={'qr_token': self.table.qr_token}),
            self._post_payload(fulfillment_mode=Order.FulfillmentMode.TABLE, **{f'option_{self.product.id}_{self.option_group.id}': ''}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.option_group.name_ar)
        self.assertEqual(Order.objects.count(), 0)
