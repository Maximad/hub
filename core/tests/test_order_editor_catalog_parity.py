"""Unpaid order editing cannot advertise products forbidden in staff POS."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Category, Order, OrderItem, Product


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class OrderEditorCatalogParityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        admin = User.objects.create_user(username='editor-parity-admin', phone='+963998004222', role='admin')
        self.client.force_login(admin)
        category = Category.objects.create(name_ar='اختبار الكتالوج')
        self.allowed = Product.objects.create(category=category, name_ar='صنف POS مسموح', price_syp=100)
        self.hidden = Product.objects.create(
            category=category, name_ar='صنف نظام داخلي',
            price_syp=0, visible_on_pos=False, orderable_on_pos=False,
        )
        # Ordinary products synchronize channel flags with QR on save.
        # Model a product already hidden by the dedicated/internal catalog
        # workflow without triggering that ordinary-product synchronization.
        Product.objects.filter(pk=self.hidden.pk).update(
            visible_on_pos=False, orderable_on_pos=False,
        )
        self.hidden.refresh_from_db()
        self.order = Order.objects.create(status=Order.Status.NEW)
        OrderItem.objects.create(
            order=self.order, product=self.hidden,
            product_name_ar_snapshot=self.hidden.name_ar, quantity=1,
            unit_price_syp_snapshot=0, line_total_syp_snapshot=0,
        )

    def test_editor_picker_excludes_hidden_product_while_preserving_history(self):
        response = self.client.get(reverse('staff_order_edit', args=[self.order.public_code]))
        self.assertEqual(response.status_code, 200)
        picker_ids = {p.pk for p in response.context['products']}
        self.assertIn(self.allowed.pk, picker_ids)
        self.assertNotIn(self.hidden.pk, picker_ids)
        # Historical, already linked items remain visible and are not altered.
        rows = response.context['item_rows']
        self.assertTrue(any(row['item'].product_id == self.hidden.pk for row in rows))
        self.assertEqual(OrderItem.objects.filter(order=self.order).count(), 1)

    def test_editor_post_cannot_add_hidden_or_special_product(self):
        response = self.client.post(reverse('staff_order_edit_add_item', args=[self.order.public_code]), {
            'product_id': str(self.hidden.pk), 'quantity': '1',
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(OrderItem.objects.filter(order=self.order).count(), 1)
