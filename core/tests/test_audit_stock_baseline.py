"""Acceptance checks for the read-only, non-forecast stock baseline."""
import json
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Category, InventoryItem, Order, OrderItem, Product, ProductRecipeItem


class StockBaselineCommandTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name_ar="مشروبات")
        self.product = Product.objects.create(
            category=category, name_ar="قهوة عربية", price_syp=150, is_available=True,
        )
        self.inventory = InventoryItem.objects.create(
            name_ar="بن عربي", code="COFFEE-AR", unit=InventoryItem.Unit.KG,
            current_quantity=Decimal("0"), estimated_unit_cost_syp=None,
        )
        self.recipe = ProductRecipeItem.objects.create(
            product=self.product, inventory_item=self.inventory,
            unit=InventoryItem.Unit.KG, quantity_per_unit=Decimal("0.010"), is_active=False,
        )

    def report(self, *options):
        out = StringIO()
        call_command("audit_stock_baseline", "--json", *options, stdout=out)
        return json.loads(out.getvalue())

    def test_zero_and_missing_cost_are_unknowns_not_stockout_claims(self):
        data = self.report()
        self.assertTrue(data["read_only"])
        self.assertFalse(data["forecast_ready"])
        self.assertEqual(data["summary"]["recorded_zero_balances"], 1)
        self.assertEqual(data["summary"]["missing_reference_cost"], 1)
        self.assertEqual(data["summary"]["active_recipe_lines"], 0)
        self.assertEqual(data["summary"]["available_products_without_active_recipes"], 1)
        p = data["priority_products"][0]
        self.assertEqual(p["readiness"], "inactive_recipe_needs_review")
        self.assertEqual(p["ingredients"][0]["recorded_zero_quantity"], True)
        self.inventory.refresh_from_db()
        self.recipe.refresh_from_db()
        self.assertEqual(self.inventory.current_quantity, Decimal("0"))
        self.assertIsNone(self.inventory.estimated_unit_cost_syp)
        self.assertFalse(self.recipe.is_active)

    def test_historical_aggregate_is_separated_from_recent_operational_sales(self):
        real_order = Order.objects.create(status=Order.Status.SERVED)
        aggregate_order = Order.objects.create(
            status=Order.Status.SERVED, notes="[PRE_SYSTEM_HISTORICAL_SALES] month=2026-08"
        )
        cancelled_order = Order.objects.create(status=Order.Status.CANCELLED)
        for order, quantity, note in (
            (real_order, 2, ""),
            (aggregate_order, 25, "import_key=2026-08:coffee"),
            (cancelled_order, 40, ""),
        ):
            OrderItem.objects.create(
                order=order, product=self.product, quantity=quantity,
                product_name_ar_snapshot="قهوة عربية",
                unit_price_syp_snapshot=150,
                item_note=note,
            )
        result = self.report()
        self.assertEqual(result["summary"]["order_lines"], 3)
        self.assertEqual(result["summary"]["served_operational_lines_in_window"], 1)
        self.assertEqual(result["summary"]["served_operational_units_in_window"], 2)
        self.assertEqual(result["summary"]["served_legacy_aggregate_lines"], 1)
        self.assertEqual(result["summary"]["served_legacy_aggregate_units"], 25)
        self.assertEqual(result["priority_products"][0]["served_line_units_in_window"], 2)
        self.assertEqual(result["priority_products"][0]["legacy_import_served_units"], 25)

    def test_invalid_bounds_and_unknown_switch_are_rejected(self):
        with self.assertRaises(CommandError):
            call_command("audit_stock_baseline", "--limit", "0")
        with self.assertRaises(CommandError):
            call_command("audit_stock_baseline", "--days", "0")
        with self.assertRaises(CommandError):
            call_command("audit_stock_baseline", "--apply")
