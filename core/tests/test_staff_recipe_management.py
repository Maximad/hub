from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Category, InventoryItem, Product, ProductRecipeItem


class StaffRecipeManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='recipe-admin',
            phone='+963900001001',
            password='test-pass',
            role=User.Role.ADMIN,
        )
        self.waiter = User.objects.create_user(
            username='recipe-waiter',
            phone='+963900001002',
            password='test-pass',
            role=User.Role.WAITER,
        )
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='مشروب اختبار',
            price_syp=1000,
        )
        self.sugar = InventoryItem.objects.create(
            name_ar='سكر',
            unit=InventoryItem.Unit.G,
            estimated_unit_cost_syp=Decimal('100.00'),
        )
        self.milk = InventoryItem.objects.create(
            name_ar='حليب',
            unit=InventoryItem.Unit.ML,
            estimated_unit_cost_syp=Decimal('50.00'),
        )

    def _login_admin(self):
        self.client.force_login(self.admin)

    def _row_payload(
        self,
        *,
        line_id='',
        inventory_item=None,
        quantity='1',
        unit=None,
        waste='0',
        active='1',
        notes='',
        action='keep',
    ):
        inventory_item = inventory_item or self.sugar
        unit = unit or inventory_item.unit
        return {
            'line_id': [str(line_id) if line_id else ''],
            'inventory_item': [str(inventory_item.pk)],
            'quantity_per_unit': [str(quantity)],
            'unit': [unit],
            'waste_factor_percent': [str(waste)],
            'is_active': [active],
            'notes': [notes],
            'action': [action],
        }

    def test_inventory_staff_can_open_recipe_manager(self):
        self._login_admin()
        response = self.client.get(reverse('staff_recipe_manager'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'إدارة الوصفات')
        self.assertContains(response, self.product.name_ar)

    def test_staff_without_inventory_capability_cannot_open_manager(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_recipe_manager'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('staff_home'))

    def test_can_add_recipe_line_and_recalculate_product_cost(self):
        self._login_admin()
        response = self.client.post(
            reverse('staff_recipe_edit', args=[self.product.pk]),
            self._row_payload(quantity='2', waste='10'),
        )
        self.assertRedirects(
            response,
            reverse('staff_recipe_edit', args=[self.product.pk]),
            fetch_redirect_response=False,
        )
        line = ProductRecipeItem.objects.get(product=self.product)
        self.assertEqual(line.inventory_item, self.sugar)
        self.assertEqual(line.quantity_per_unit, Decimal('2'))
        self.assertEqual(line.waste_factor_percent, Decimal('10'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.estimated_unit_cost_syp, 220)

    def test_can_update_existing_recipe_line(self):
        line = ProductRecipeItem.objects.create(
            product=self.product,
            inventory_item=self.sugar,
            quantity_per_unit=Decimal('1'),
            unit=self.sugar.unit,
            waste_factor_percent=Decimal('0'),
        )
        self._login_admin()
        self.client.post(
            reverse('staff_recipe_edit', args=[self.product.pk]),
            self._row_payload(
                line_id=line.pk,
                quantity='3.5',
                waste='5',
                notes='كمية جديدة',
            ),
        )
        line.refresh_from_db()
        self.assertEqual(line.quantity_per_unit, Decimal('3.5'))
        self.assertEqual(line.waste_factor_percent, Decimal('5'))
        self.assertEqual(line.notes, 'كمية جديدة')

    def test_duplicate_item_and_unit_is_rejected_without_partial_save(self):
        self._login_admin()
        payload = {
            'line_id': ['', ''],
            'inventory_item': [str(self.sugar.pk), str(self.sugar.pk)],
            'quantity_per_unit': ['1', '2'],
            'unit': [self.sugar.unit, self.sugar.unit],
            'waste_factor_percent': ['0', '0'],
            'is_active': ['1', '1'],
            'notes': ['', ''],
            'action': ['keep', 'keep'],
        }
        response = self.client.post(
            reverse('staff_recipe_edit', args=[self.product.pk]),
            payload,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لا يمكن تكرار نفس مادة المخزون')
        self.assertFalse(ProductRecipeItem.objects.filter(product=self.product).exists())

    def test_delete_recipe_line_and_recalculate_cost(self):
        line = ProductRecipeItem.objects.create(
            product=self.product,
            inventory_item=self.sugar,
            quantity_per_unit=Decimal('1'),
            unit=self.sugar.unit,
        )
        self.product.estimated_unit_cost_syp = 100
        self.product.save(update_fields=['estimated_unit_cost_syp'])
        self._login_admin()
        response = self.client.post(
            reverse('staff_recipe_edit', args=[self.product.pk]),
            self._row_payload(line_id=line.pk, action='delete'),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductRecipeItem.objects.filter(pk=line.pk).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.estimated_unit_cost_syp, 0)

    def test_recipe_edit_never_accepts_line_from_another_product(self):
        other = Product.objects.create(
            category=self.category,
            name_ar='منتج آخر',
            price_syp=800,
        )
        foreign_line = ProductRecipeItem.objects.create(
            product=other,
            inventory_item=self.milk,
            quantity_per_unit=Decimal('1'),
            unit=self.milk.unit,
        )
        self._login_admin()
        response = self.client.post(
            reverse('staff_recipe_edit', args=[self.product.pk]),
            self._row_payload(
                line_id=foreign_line.pk,
                inventory_item=self.milk,
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لا يتبع هذا المنتج')
        foreign_line.refresh_from_db()
        self.assertEqual(foreign_line.product, other)
