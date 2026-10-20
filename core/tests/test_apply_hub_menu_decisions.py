"""Regression tests for bounded menu correction and preview/commit safety."""
import json
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import connection
from django.test import TestCase

from core.models import (
    ActivityLog, Category, InventoryItem, Order, OrderItem, Product, ProductRecipeItem,
    StockMovement, Purchase,
)


PRODUCTS = {
    58: ("شاي عادي", 50),
    84: ("عرق الريان - كأس", 200),
    107: ("قهوة عربية", 100),
    108: ("قهوة إيطالية", 150),
    109: ("كابوتشينو", 150),
    322: ("نسكافيه 3x1", 100),
    333: ("كوكتيل فودكا", 250),
    334: ("موهيتو", 350),
    339: ("شوت دودو", 150),
    362: ("اركيلة", 250),
}
INVENTORY = {
    175: ("DRK-TEA-AHMAD-BAG", "شاي أحمد عادي – ظرف", InventoryItem.Unit.PORTION),
    173: ("DRK-COF-ARABIC", "بن عربي فاتح", InventoryItem.Unit.KG),
    171: ("DRK-COF-ESPRESSO", "بن إسبريسو", InventoryItem.Unit.KG),
    45: ("ING-DAIRY-MILK", "حليب", InventoryItem.Unit.LITER),
    200: ("DRK-ALC-ARAK-RAYAN", "عرق الريان", InventoryItem.Unit.LITER),
    201: ("DRK-ALC-VODKA-SMIRNOFF", "فودكا سميرنوف", InventoryItem.Unit.LITER),
    206: ("DRK-ALC-RUM-WHITE", "روم أبيض", InventoryItem.Unit.LITER),
    210: ("ING-PICKLE-GREENOLIVE", "زيتون أخضر", InventoryItem.Unit.KG),
    172: ("DRK-COF-INSTANT", "قهوة سريعة التحضير", InventoryItem.Unit.KG),
    125: ("ING-SAUCE-TABASCO", "تاباسكو", InventoryItem.Unit.ML),
    5: ("ING-FRT-LEMON", "ليمون", InventoryItem.Unit.KG),
}


class HubReviewedMenuDecisionsTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name_ar="مشروبات", name_en="Drinks")
        for pk, (name, price) in PRODUCTS.items():
            Product.objects.create(
                pk=pk, category=cat, name_ar=name, price_syp=price,
                is_available=(pk != 322),
                visible_on_pos=(pk != 322),
                orderable_on_pos=(pk != 322),
            )
        for pk, (code, name, unit) in INVENTORY.items():
            InventoryItem.objects.create(
                pk=pk, name_ar=name, code=code, unit=unit,
                current_quantity=Decimal("7"), estimated_unit_cost_syp=Decimal("5.00"),
            )
        for prod, inv, qty in [
            (58, 175, ".001"),
            (84, 200, ".050"),
            (107, 173, ".010"),
            (108, 171, ".018"),
            (109, 171, ".018"),
            (109, 45, ".150"),
            (322, 172, ".005"),
            (333, 201, ".050"),
            (334, 206, ".050"),
            (339, 125, "2.000"),
            (339, 201, ".040"),
            (339, 210, ".015"),
        ]:
            inv_item = InventoryItem.objects.get(pk=inv)
            ProductRecipeItem.objects.create(
                product_id=prod, inventory_item=inv_item,
                unit=inv_item.unit, quantity_per_unit=Decimal(qty), is_active=False,
            )
        # Test fixtures use the real production IDs to exercise hard identity
        # guards. Explicit PostgreSQL PK inserts do not advance serial sequences.
        # Reset just these two *test database* sequences before the command
        # auto-creates new products and inventory items.
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(
                no_style(), [Product, InventoryItem],
            ):
                cursor.execute(sql)
        self.operator = get_user_model().objects.create_user(
            username="menu-operator", password="test-password", is_staff=True,
        )
        order = Order.objects.create()
        self.order_item = OrderItem.objects.create(
            order=order, product_id=107, quantity=1,
            product_name_ar_snapshot="قهوة عربية", unit_price_syp_snapshot=100,
        )

    def run_decisions(self, *args):
        output = StringIO()
        call_command("apply_hub_menu_decisions", *args, "--json", stdout=output)
        return json.loads(output.getvalue())

    def test_default_preview_is_rollback_only(self):
        before = {
            "products": Product.objects.count(),
            "inventory": InventoryItem.objects.count(),
            "recipes": ProductRecipeItem.objects.count(),
            "logs": ActivityLog.objects.count(),
        }
        report = self.run_decisions()
        self.assertTrue(report["read_only"])
        self.assertFalse(report["applied"])
        self.assertGreater(report["counts"]["created"], 5)
        self.assertEqual(Product.objects.count(), before["products"])
        self.assertEqual(InventoryItem.objects.count(), before["inventory"])
        self.assertEqual(ProductRecipeItem.objects.count(), before["recipes"])
        self.assertEqual(ActivityLog.objects.count(), before["logs"])
        self.assertFalse(Product.objects.get(pk=322).is_available)

    def test_explicit_apply_preserves_history_stock_prices_and_inactive_recipes(self):
        report = self.run_decisions("--apply", "--actor", "menu-operator")
        self.assertTrue(report["applied"])
        self.assertFalse(report["read_only"])
        self.assertTrue(Product.objects.get(pk=322).is_available)
        self.assertTrue(Product.objects.get(pk=322).visible_on_pos)
        self.assertTrue(Product.objects.get(pk=322).orderable_on_pos)
        self.assertEqual(Product.objects.get(pk=322).price_syp, 100)
        self.assertEqual(Product.objects.get(pk=108).price_syp, 150)
        for name in [
            "سحلب", "شاي ظرف", "شاي خمير - كأس", "عرق الريان - كأس كبير"
        ]:
            product = Product.objects.get(name_ar=name)
            self.assertFalse(product.is_available)
            self.assertFalse(product.visible_on_qr)
            self.assertFalse(product.orderable_on_pos)
            self.assertEqual(product.price_syp, 0)
        self.assertTrue(InventoryItem.objects.filter(code="ING-PICKLE-STUFFED-OLIVE").exists())
        self.assertTrue(InventoryItem.objects.filter(code="ING-PREP-FRESH-LEMON-JUICE").exists())
        self.assertTrue(InventoryItem.objects.filter(code="DRK-HOOKAH-TOBACCO-PENDING", is_active=False).exists())
        self.assertEqual(
            ProductRecipeItem.objects.get(product_id=108, inventory_item_id=171).quantity_per_unit,
            Decimal(".016")
        )
        self.assertEqual(
            ProductRecipeItem.objects.get(product_id=333, inventory_item_id=201).quantity_per_unit,
            Decimal(".075")
        )
        self.assertEqual(
            ProductRecipeItem.objects.get(product_id=339, inventory_item_id=201).quantity_per_unit,
            Decimal(".020")
        )
        self.assertEqual(
            ProductRecipeItem.objects.get(
                product_id=339,
                inventory_item__code="ING-PREP-FRESH-LEMON-JUICE",
            ).quantity_per_unit,
            Decimal(".020")
        )
        self.assertIn(
            "نقطتان", ProductRecipeItem.objects.get(
                product_id=339, inventory_item_id=125,
            ).notes
        )
        self.assertIn(
            "غير معتمد", ProductRecipeItem.objects.get(
                product_id=334, inventory_item_id=206,
            ).notes
        )
        self.assertFalse(ProductRecipeItem.objects.filter(is_active=True).exists())
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(Purchase.objects.count(), 0)
        self.assertEqual(self.order_item.unit_price_syp_snapshot, 100)
        for item in InventoryItem.objects.filter(pk__in=INVENTORY):
            self.assertEqual(item.current_quantity, Decimal("7"))
            self.assertEqual(item.estimated_unit_cost_syp, Decimal("5"))

    def test_repeat_apply_is_idempotent(self):
        self.run_decisions("--apply", "--actor", "menu-operator")
        before = (
            Product.objects.count(),
            InventoryItem.objects.count(),
            ProductRecipeItem.objects.count(),
            ActivityLog.objects.count(),
        )
        report = self.run_decisions("--apply", "--actor", "menu-operator")
        self.assertEqual(
            (
                Product.objects.count(),
                InventoryItem.objects.count(),
                ProductRecipeItem.objects.count(),
                ActivityLog.objects.count(),
            ),
            before,
        )
        self.assertEqual(report["counts"].get("created", 0), 0)
        self.assertEqual(report["counts"].get("updated", 0), 0)

    def test_apply_requires_identified_active_staff_actor(self):
        with self.assertRaises(CommandError):
            call_command("apply_hub_menu_decisions", "--apply")
        with self.assertRaises(CommandError):
            call_command(
                "apply_hub_menu_decisions", "--apply", "--actor", "unknown-operator"
            )

    def test_identity_mismatch_rolls_back_all_creations(self):
        Product.objects.filter(pk=334).update(name_ar="Renamed product")
        with self.assertRaises(CommandError):
            self.run_decisions("--apply", "--actor", "menu-operator")
        self.assertFalse(InventoryItem.objects.filter(code="DRK-SAHLEB-WIN-SACHET").exists())
        self.assertFalse(Product.objects.get(pk=322).is_available)


    def test_known_inventory_unit_mismatch_aborts_before_writes(self):
        InventoryItem.objects.filter(pk=171).update(unit=InventoryItem.Unit.ML)
        with self.assertRaises(CommandError):
            self.run_decisions("--apply", "--actor", "menu-operator")
        self.assertFalse(InventoryItem.objects.filter(code="DRK-SAHLEB-WIN-SACHET").exists())
        self.assertFalse(Product.objects.get(pk=322).is_available)

    def test_preexisting_active_recipe_aborts_entire_transaction(self):
        ProductRecipeItem.objects.filter(product_id=108, inventory_item_id=171).update(
            is_active=True
        )
        with self.assertRaises(CommandError):
            self.run_decisions("--apply", "--actor", "menu-operator")
        self.assertFalse(InventoryItem.objects.filter(code="DRK-SAHLEB-WIN-SACHET").exists())
        self.assertFalse(Product.objects.get(pk=322).is_available)
