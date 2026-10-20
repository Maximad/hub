"""Staff-approved price onboarding is preview-first, atomic and scoped."""
import csv
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import ActivityLog, Category, InventoryItem, Product, ProductRecipeItem, StockMovement
from core.management.commands.review_hub_new_product_prices import APPROVED, FIELDS


class HubNewProductPriceWorkflowTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar="مشروبات")
        self.products = {}
        for key, name in APPROVED.items():
            self.products[key] = Product.objects.create(
                category=self.category, name_ar=name, price_syp=0,
                is_available=False, visible_on_qr=False, orderable_on_qr=False,
                visible_on_pos=False, orderable_on_pos=False,
                metadata={
                    "hub_decision_key": key,
                    "status": "awaiting_verified_price_and_recipe",
                },
            )
        self.other = Product.objects.create(
            category=self.category, name_ar="منتج قديم", price_syp=300,
            is_available=True, visible_on_qr=True, orderable_on_qr=True,
        )
        self.item = InventoryItem.objects.create(
            name_ar="بن", code="PRICE-TEST-BEANS",
            unit=InventoryItem.Unit.KG,
            current_quantity=5,
        )
        ProductRecipeItem.objects.create(
            product=self.products["sahleb"], inventory_item=self.item,
            unit=self.item.unit, quantity_per_unit="0.020", is_active=False,
        )
        self.actor = get_user_model().objects.create_user(
            username="menu-price-operator", password="test-password", is_staff=True,
        )
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.csvpath = Path(self.temp.name) / "prices.csv"

    def write_file(self, rows):
        with self.csvpath.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            writer.writerows(rows)
        return self.csvpath

    def row(self, key, price="160", publish="no", expected="0"):
        return [key, APPROVED[key], expected, price, publish]

    def run_review(self, *arguments):
        result = StringIO()
        call_command(
            "review_hub_new_product_prices", *arguments, "--json", stdout=result,
        )
        return json.loads(result.getvalue())

    def test_template_contains_only_four_known_unpriced_products(self):
        output = StringIO()
        call_command("review_hub_new_product_prices", "--template", stdout=output)
        rows = list(csv.DictReader(StringIO(output.getvalue())))
        self.assertEqual({row["hub_decision_key"] for row in rows}, set(APPROVED))
        self.assertTrue(all(row["new_price_syp"] == "" for row in rows))
        self.assertTrue(all(row["publish"] == "no" for row in rows))

    def test_default_preview_rolls_back_price_visibility_and_log(self):
        path = self.write_file([self.row("sahleb", "140", "yes")])
        report = self.run_review("--file", str(path))
        self.assertTrue(report["read_only"])
        self.assertEqual(report["counts"], {"updated": 1})
        product = Product.objects.get(pk=self.products["sahleb"].pk)
        self.assertEqual(product.price_syp, 0)
        self.assertFalse(product.visible_on_qr)
        self.assertFalse(product.visible_on_pos)
        self.assertFalse(ActivityLog.objects.filter(
            action="hub_new_product_price_approval_2026_10",
        ).exists())

    def test_apply_hidden_price_then_explicit_publish_preserves_stock_and_recipe(self):
        path = self.write_file([self.row("sahleb", "140", "no")])
        report = self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        self.assertTrue(report["applied"])
        product = Product.objects.get(pk=self.products["sahleb"].pk)
        self.assertEqual(product.price_syp, 140)
        self.assertFalse(product.is_available)
        self.assertFalse(product.orderable_on_qr)
        self.assertFalse(product.orderable_on_pos)

        # A newer template carries the existing price as the expected value.
        path = self.write_file([self.row("sahleb", "140", "yes", "140")])
        report = self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        self.assertEqual(report["counts"], {"updated": 1})
        product.refresh_from_db()
        self.assertTrue(product.is_available)
        self.assertTrue(product.visible_on_qr)
        self.assertTrue(product.orderable_on_qr)
        self.assertTrue(product.visible_on_pos)
        self.assertTrue(product.orderable_on_pos)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(InventoryItem.objects.get(pk=self.item.pk).current_quantity, 5)
        self.assertFalse(ProductRecipeItem.objects.get(product=product).is_active)
        self.assertEqual(Product.objects.get(pk=self.other.pk).price_syp, 300)

        before_log_count = ActivityLog.objects.count()
        report = self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        self.assertEqual(report["counts"], {"unchanged": 1})
        self.assertEqual(ActivityLog.objects.count(), before_log_count)

    def test_stale_second_row_rolls_back_first_row_and_log(self):
        self.products["tea_bag"].price_syp = 90
        self.products["tea_bag"].save(update_fields=["price_syp", "updated_at"])
        path = self.write_file([
            self.row("sahleb", "140", "yes"),
            self.row("tea_bag", "150", "no", "0"),
        ])
        with self.assertRaisesMessage(CommandError, "stale price"):
            self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        product = Product.objects.get(pk=self.products["sahleb"].pk)
        self.assertEqual(product.price_syp, 0)
        self.assertFalse(product.is_available)
        self.assertEqual(
            ActivityLog.objects.filter(action="hub_new_product_price_approval_2026_10").count(), 0,
        )

    def test_rejects_duplicate_unknown_bad_publish_zero_and_nonstaff_actor(self):
        cases = [
            [self.row("sahleb"), self.row("sahleb")],
            [["unknown_key", "Anything", "0", "150", "yes"]],
            [self.row("sahleb", "140", "maybe")],
            [self.row("sahleb", "0", "yes")],
            [self.row("sahleb", "-10", "no")],
            [self.row("sahleb", "1.5", "no")],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                path = self.write_file(rows)
                with self.assertRaises(CommandError):
                    self.run_review("--file", str(path))
        path = self.write_file([self.row("sahleb")])
        with self.assertRaises(CommandError):
            self.run_review("--file", str(path), "--apply")
        with self.assertRaises(CommandError):
            self.run_review("--file", str(path), "--apply", "--actor", "unknown")

    def test_refuses_unpublish_or_reprice_of_live_product(self):
        path = self.write_file([self.row("sahleb", "120", "yes")])
        self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        path = self.write_file([self.row("sahleb", "120", "no", "120")])
        with self.assertRaises(CommandError):
            self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        path = self.write_file([self.row("sahleb", "150", "yes", "120")])
        with self.assertRaises(CommandError):
            self.run_review("--file", str(path), "--apply", "--actor", self.actor.username)
        self.assertEqual(Product.objects.get(pk=self.products["sahleb"].pk).price_syp, 120)
