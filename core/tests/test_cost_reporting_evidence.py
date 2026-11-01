"""Read-only reports must distinguish estimates from missing cost/count evidence."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditEvent, Category, InventoryItem, Product, ProductRecipeItem


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class CostReportingEvidenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client.force_login(User.objects.create_user(
            username='cost-evidence-admin', phone='+963998004224', role='admin',
        ))
        self.category = Category.objects.create(name_ar='قسم كلفة الاختبار')
        self.product = Product.objects.create(
            category=self.category, name_ar='منتج كلفة الاختبار',
            price_syp=500, requires_preparation=True,
        )
        self.item = InventoryItem.objects.create(
            name_ar='مادة كلفة الاختبار', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('3'), estimated_unit_cost_syp=None,
        )

    def _recipe_row(self):
        response = self.client.get(reverse('staff_recipe_report'))
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.context['rows'] if row['product'].pk == self.product.pk)

    def _stock_row(self):
        response = self.client.get(reverse('staff_inventory_reports'))
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.context['stock_rows'] if row['item'].pk == self.item.pk)

    def test_empty_draft_and_missing_cost_recipe_never_expose_full_price_as_margin(self):
        empty = self._recipe_row()
        self.assertFalse(empty['cost_display_ready'])
        self.assertEqual(empty['recipe']['recipe_margin_syp'], Decimal('500'))
        page = self.client.get(reverse('staff_recipe_report'))
        self.assertContains(page, '— غير متاح')
        editor = self.client.get(reverse('staff_recipe_edit', args=[self.product.pk]))
        self.assertFalse(editor.context['cost_display_ready'])
        self.assertContains(editor, '— غير مكتملة')

        line = ProductRecipeItem.objects.create(
            product=self.product, inventory_item=self.item,
            unit=InventoryItem.Unit.KG, quantity_per_unit=Decimal('1'),
        )
        self.assertFalse(self._recipe_row()['cost_display_ready'])
        self.item.estimated_unit_cost_syp = Decimal('40')
        self.item.save(update_fields=['estimated_unit_cost_syp'])
        ready = self._recipe_row()
        self.assertTrue(ready['cost_display_ready'])
        self.assertEqual(ready['recipe']['recipe_margin_syp'], Decimal('460'))
        line.is_active = False
        line.save(update_fields=['is_active'])
        self.assertFalse(self._recipe_row()['cost_display_ready'])

    def test_legacy_stock_with_reference_price_is_still_unknown_until_count_matched(self):
        self.item.estimated_unit_cost_syp = Decimal('30')
        self.item.save(update_fields=['estimated_unit_cost_syp'])
        self.assertFalse(self._stock_row()['can_estimate_value'])
        self.assertContains(self.client.get(reverse('staff_inventory_reports')), '— غير معروف')
        AuditEvent.objects.create(
            channel='staff-stock-count', action='inventory_physical_count_verified',
            before_snapshot={'item_id': self.item.pk, 'unit': self.item.unit},
            after_snapshot={'quantity': '3'},
        )
        counted = self._stock_row()
        self.assertTrue(counted['can_estimate_value'])
        self.assertEqual(counted['estimated_value_syp'], Decimal('90'))
        self.item.current_quantity = Decimal('4')
        self.item.save(update_fields=['current_quantity'])
        self.assertEqual(self._stock_row()['quantity_evidence']['state'], 'quantity_differs')
        self.assertFalse(self._stock_row()['can_estimate_value'])

    def test_confirmed_zero_is_numeric_zero_but_missing_unit_cost_is_unknown(self):
        self.item.current_quantity = Decimal('0')
        self.item.save(update_fields=['current_quantity'])
        AuditEvent.objects.create(
            channel='staff-stock-count', action='inventory_physical_count_verified',
            before_snapshot={'item_id': self.item.pk, 'unit': self.item.unit},
            after_snapshot={'quantity': '0'},
        )
        self.assertFalse(self._stock_row()['can_estimate_value'])
        self.item.estimated_unit_cost_syp = Decimal('30')
        self.item.save(update_fields=['estimated_unit_cost_syp'])
        row = self._stock_row()
        self.assertTrue(row['can_estimate_value'])
        self.assertEqual(row['estimated_value_syp'], Decimal('0'))
