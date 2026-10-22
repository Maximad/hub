"""Acceptance checks for the read-only, non-forecast stock baseline."""
import json
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import (AuditEvent, Category, InventoryItem, Order, OrderItem, Product,
                         ProductRecipeItem, StockMovement, Purchase, Payment, PostingBatch)


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


    def test_pilot_prefers_real_recent_sales_over_huge_historical_aggregate(self):
        category = self.product.category
        recent = Product.objects.create(category=category, name_ar="شاي مبيعات يومية",
            price_syp=100, is_available=True, product_type=Product.ProductType.DRINK,
            item_type=Product.ItemType.BEVERAGE)
        legacy = Product.objects.create(category=category, name_ar="قهوة تاريخية",
            price_syp=100, is_available=True, product_type=Product.ProductType.DRINK,
            item_type=Product.ItemType.BEVERAGE)
        service = Product.objects.create(category=category, name_ar="إنترنت",
            price_syp=100, is_available=True, product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE)
        hidden = Product.objects.create(category=category, name_ar="منتج غير متاح",
            price_syp=100, is_available=False, product_type=Product.ProductType.DRINK)
        live_order = Order.objects.create(status=Order.Status.SERVED)
        historical = Order.objects.create(status=Order.Status.SERVED,
            notes="[PRE_SYSTEM_HISTORICAL_SALES] month=2026-08")
        for product, order, quantity in [
            (recent, live_order, 2), (legacy, historical, 500),
            (service, live_order, 100), (hidden, live_order, 200),
        ]:
            OrderItem.objects.create(
                order=order, product=product, quantity=quantity,
                product_name_ar_snapshot=product.name_ar, unit_price_syp_snapshot=100,
            )
        before = tuple(model.objects.count() for model in
            (InventoryItem, ProductRecipeItem, StockMovement, Purchase, Payment, PostingBatch))
        result = self.report("--pilot")
        proposals = result["pilot_products"]
        self.assertEqual([p["product_id"] for p in proposals[:2]],
                         [recent.pk, legacy.pk])
        self.assertNotIn(service.pk, [p["product_id"] for p in proposals])
        self.assertNotIn(hidden.pk, [p["product_id"] for p in proposals])
        self.assertEqual(proposals[0]["sales_evidence"], "recent_served_order_lines_not_payment_proof")
        self.assertEqual(proposals[1]["sales_evidence"], "historical_import_only_not_demand_proof")
        self.assertEqual(proposals[1]["served_units_in_window"], 0)
        self.assertEqual(proposals[1]["legacy_aggregate_units_excluded_from_ranking"], 500)
        self.assertEqual(result["summary"]["served_operational_units_in_window"], 302)
        self.assertEqual(result["summary"]["served_legacy_aggregate_units"], 500)
        self.assertFalse(proposals[0]["pilot_approved"])
        self.assertFalse(result["pilot_selection"]["legacy_imports_ranked_as_sales"])
        self.assertEqual(
            before,
            tuple(model.objects.count() for model in
                (InventoryItem, ProductRecipeItem, StockMovement, Purchase, Payment, PostingBatch)),
        )

    def test_ingredient_review_reuses_count_provenance_without_certifying_cost(self):
        missing = self.report("--pilot")["pilot_products"][0]
        ingredient = missing["ingredients"][0]
        self.assertEqual(ingredient["inventory_code"], "COFFEE-AR")
        self.assertEqual(ingredient["count_provenance"], "no_snapshot")
        self.assertIn("physical_count_required", ingredient["issues"])
        self.assertIn("missing_reference_cost", ingredient["issues"])
        self.assertIn("invoice_cost_not_verified", ingredient["issues"])
        self.assertIn("review_inactive_recipe_not_auto_activate", missing["review_reasons"])
        AuditEvent.objects.create(
            action="inventory_physical_count_verified", channel="staff-stock-count",
            before_snapshot={"item_id": self.inventory.pk, "unit": "kg"},
            after_snapshot={"item_id": self.inventory.pk, "quantity": "0"},
        )
        self.inventory.estimated_unit_cost_syp = Decimal("120.00")
        self.inventory.save(update_fields=["estimated_unit_cost_syp"])
        matched = self.report("--pilot")["pilot_products"][0]["ingredients"][0]
        self.assertEqual(matched["count_provenance"], "matches_count")
        self.assertNotIn("physical_count_required", matched["issues"])
        self.assertNotIn("missing_reference_cost", matched["issues"])
        self.assertIn("invoice_cost_not_verified", matched["issues"])
        self.recipe.refresh_from_db()
        self.assertFalse(self.recipe.is_active)
        self.assertEqual(self.inventory.current_quantity, Decimal("0"))

    def test_pilot_explains_unpriced_missing_sku_and_unit_mismatch(self):
        self.product.price_syp = 0
        self.product.visible_on_qr = False
        self.product.orderable_on_qr = False
        self.product.visible_on_pos = False
        self.product.orderable_on_pos = False
        self.product.save()
        self.inventory.code = None
        self.inventory.save()
        self.recipe.unit = InventoryItem.Unit.G
        self.recipe.save()
        candidate = self.report("--pilot")["pilot_products"][0]
        self.assertIn("review_unpriced_product", candidate["review_reasons"])
        self.assertIn("review_sales_visibility", candidate["review_reasons"])
        issues = candidate["ingredients"][0]["issues"]
        self.assertIn("map_missing_sku", issues)
        self.assertIn("review_unit_mismatch", issues)
        self.assertEqual(candidate["product_id"], self.product.pk)

    def test_pilot_is_opt_in_and_bounds_and_unknown_apply_are_refused(self):
        report = self.report()
        self.assertNotIn("pilot_products", report)
        with self.assertRaises(CommandError):
            call_command("audit_stock_baseline", "--pilot", "--limit", "5")
        with self.assertRaises(CommandError):
            call_command("audit_stock_baseline", "--pilot", "--apply")
