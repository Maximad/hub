"""POS requires an explicit bill choice for a table with an existing account."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection
from core.models import Category, HubVisit, Order, OrderItem, Product, Room, TableArea


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class PosExplicitAccountChoiceTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(
            username='pos-explicit-account-owner', phone='+963999222551', role='admin',
        ))
        room = Room.objects.create(name_ar='قاعة اختبار الحساب')
        self.table = TableArea.objects.create(room=room, name_ar='طاولة حساب')
        self.other_table = TableArea.objects.create(room=room, name_ar='طاولة مستقلة')
        self.visit = HubVisit.objects.create(table=self.table)
        category = Category.objects.create(name_ar='اختبار حساب POS')
        section = MenuSection.objects.create(name_ar='قهوة')
        self.product = Product.objects.create(
            category=category, name_ar='قهوة الحساب', price_syp=100,
            is_available=True, visible_on_qr=True, orderable_on_qr=True,
        )
        self.product.menu_sections.add(section)
        self.url = reverse('staff_pos')

    def payload(self, table=None, visit_id=''):
        return {
            'table_id': str((table or self.table).pk),
            'fulfillment_mode': Order.FulfillmentMode.TABLE,
            'visit_id': visit_id,
            f'qty_{self.product.pk}': '1',
        }

    def test_existing_table_account_requires_explicit_choice(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لهذه الطاولة حساب مفتوح')
        self.assertEqual(Order.objects.count(), 0)

        separate = self.client.post(self.url, self.payload(visit_id='separate'))
        self.assertEqual(separate.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.table_id, self.table.pk)
        self.assertIsNone(order.visit_id)

    def test_matching_visit_is_linked_and_other_table_mismatch_is_rejected(self):
        linked = self.client.post(self.url, self.payload(visit_id=str(self.visit.pk)))
        self.assertEqual(linked.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.visit_id, self.visit.pk)
        self.assertEqual(order.table_id, self.table.pk)
        wrong = self.client.post(self.url, self.payload(
            table=self.other_table, visit_id=str(self.visit.pk),
        ))
        self.assertEqual(wrong.status_code, 200)
        self.assertContains(wrong, 'لا تطابق طاولة الحساب المفتوح')
        self.assertEqual(Order.objects.count(), 1)

    def test_filtered_account_options_show_table_and_current_balance(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-table-id="{self.table.pk}"')
        self.assertContains(response, 'فاتورة منفصلة جديدة — اختيار صريح')
        self.assertContains(response, 'pos-account-status')
        self.assertContains(response, 'staff-pos__optional-details')
        self.assertContains(response, 'staff-pos__member-search')
