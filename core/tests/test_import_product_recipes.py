from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from core.models import Category, InventoryItem, Product, ProductRecipeItem
from core.tests.test_import_inventory_items import write_xlsx

HEADERS = [
    'حالة الاستيراد', 'اسم المنتج', 'مفتاح المنتج', 'مادة المخزون', 'كود مادة المخزون',
    'الكمية المدخلة', 'وحدة الإدخال', 'وحدة المخزون', 'الكمية الموحّدة', 'نسبة الهدر %',
    'نشط', 'كلفة وحدة المخزون', 'كلفة السطر', 'ملاحظات', 'التحقق',
]


def recipe_row(**overrides):
    row = {
        'حالة الاستيراد': 'جاهز للاستيراد',
        'اسم المنتج': 'قهوة',
        'مفتاح المنتج': 'COF',
        'مادة المخزون': 'بن',
        'كود مادة المخزون': 'INV-COFFEE',
        'الكمية المدخلة': '5',
        'وحدة الإدخال': 'غ',
        'وحدة المخزون': 'كغ',
        'الكمية الموحّدة': '0.005',
        'نسبة الهدر %': '',
        'نشط': 'نعم',
        'كلفة وحدة المخزون': '',
        'كلفة السطر': '',
        'ملاحظات': 'note',
        'التحقق': 'صالح',
    }
    row.update(overrides)
    return [row[header] for header in HEADERS]


class ImportProductRecipesCommandTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='مشروبات', name_en='Drinks')
        self.product = Product.objects.create(
            category=self.category, name_ar='قهوة', name_en='Coffee', price_syp=1000,
            estimated_unit_cost_syp=123, metadata={'masharib_menu_code': 'COF'},
        )
        self.by_name = Product.objects.create(category=self.category, name_ar='شاي', name_en='Tea', price_syp=800)
        self.inventory = InventoryItem.objects.create(
            name_ar='بن', code='INV-COFFEE', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('42.000'), estimated_unit_cost_syp=Decimal('100.00'),
        )
        self.liquid = InventoryItem.objects.create(name_ar='حليب', code='INV-MILK', unit=InventoryItem.Unit.LITER, current_quantity=Decimal('7.000'))

    def run_import(self, rows, *args):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'recipes.xlsx'
            write_xlsx(path, [HEADERS, *rows], sheet_name='وصفات المنتجات')
            out, err = StringIO(), StringIO()
            call_command('import_product_recipes', str(path), *args, stdout=out, stderr=err)
            return out.getvalue(), err.getvalue()

    def test_product_matching_by_masharib_menu_code_and_inventory_code(self):
        out, err = self.run_import([recipe_row()])
        self.assertEqual(err, '')
        recipe = ProductRecipeItem.objects.get(product=self.product, inventory_item=self.inventory)
        self.assertEqual(recipe.quantity_per_unit, Decimal('0.005'))
        self.assertEqual(recipe.unit, InventoryItem.Unit.KG)
        self.assertEqual(recipe.notes, 'note')
        self.assertIn('created=1', out)

    def test_product_matching_by_exact_arabic_name_when_key_blank(self):
        self.run_import([recipe_row(**{'اسم المنتج': 'شاي', 'مفتاح المنتج': ''})])
        self.assertTrue(ProductRecipeItem.objects.filter(product=self.by_name, inventory_item=self.inventory).exists())

    def test_missing_and_ambiguous_products_are_reported(self):
        Product.objects.create(category=self.category, name_ar='مكرر', price_syp=1)
        Product.objects.create(category=self.category, name_ar='مكرر', price_syp=1)
        out, err = self.run_import([
            recipe_row(**{'مفتاح المنتج': 'NOPE'}),
            recipe_row(**{'اسم المنتج': 'مكرر', 'مفتاح المنتج': ''}),
        ])
        self.assertIn('missing_product=1', out)
        self.assertIn('ambiguous_product=1', out)
        self.assertIn('Row 2:', err)
        self.assertIn('Row 3:', err)

    def test_missing_inventory_is_reported_without_name_lookup(self):
        out, err = self.run_import([recipe_row(**{'مادة المخزون': 'بن', 'كود مادة المخزون': 'UNKNOWN'})])
        self.assertIn('missing_inventory=1', out)
        self.assertIn("inventory_code='UNKNOWN'", err)

    def test_g_kg_and_ml_liter_conversions(self):
        self.run_import([
            recipe_row(**{'الكمية المدخلة': '500', 'وحدة الإدخال': 'غ', 'الكمية الموحّدة': '0.5'}),
            recipe_row(**{'مادة المخزون': 'حليب', 'كود مادة المخزون': 'INV-MILK', 'الكمية المدخلة': '250', 'وحدة الإدخال': 'مل', 'وحدة المخزون': 'لتر', 'الكمية الموحّدة': '0.25'}),
        ])
        self.assertEqual(ProductRecipeItem.objects.get(inventory_item=self.inventory).quantity_per_unit, Decimal('0.500'))
        self.assertEqual(ProductRecipeItem.objects.get(inventory_item=self.liquid).quantity_per_unit, Decimal('0.250'))

    def test_unsupported_conversion_is_invalid_unit(self):
        out, err = self.run_import([recipe_row(**{'وحدة الإدخال': 'قطعة', 'الكمية الموحّدة': ''})])
        self.assertIn('invalid_unit=1', out)
        self.assertIn('Unsupported unit conversion', err)

    def test_ready_only_default_behavior_and_include_review(self):
        draft = recipe_row(**{'حالة الاستيراد': 'مسودة'})
        ignored = recipe_row(**{'حالة الاستيراد': 'تجاهل'})
        review = recipe_row(**{'حالة الاستيراد': 'بحاجة مراجعة'})
        out, _ = self.run_import([draft, ignored, review])
        self.assertIn('created=0', out)
        self.assertIn('skipped_status=3', out)
        out, _ = self.run_import([review], '--include-review')
        self.assertIn('created=1', out)

    def test_duplicate_safe_update(self):
        self.run_import([recipe_row()])
        self.run_import([recipe_row(**{'الكمية المدخلة': '6', 'الكمية الموحّدة': '0.006', 'نسبة الهدر %': '5', 'نشط': 'لا'})])
        self.assertEqual(ProductRecipeItem.objects.count(), 1)
        recipe = ProductRecipeItem.objects.get()
        self.assertEqual(recipe.quantity_per_unit, Decimal('0.006'))
        self.assertEqual(recipe.waste_factor_percent, Decimal('5.00'))
        self.assertFalse(recipe.is_active)

    def test_dry_run_rolls_back(self):
        out, _ = self.run_import([recipe_row()], '--dry-run')
        self.assertIn('created=1', out)
        self.assertEqual(ProductRecipeItem.objects.count(), 0)

    def test_inventory_quantities_and_product_cost_are_never_modified(self):
        self.run_import([recipe_row()])
        self.inventory.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.inventory.current_quantity, Decimal('42.000'))
        self.assertEqual(self.product.estimated_unit_cost_syp, 123)
