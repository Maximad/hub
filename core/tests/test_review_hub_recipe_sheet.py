"""Tests for the non-mutating Hub recipe review preflight."""
import csv
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Category, InventoryItem, Product, ProductRecipeItem
from core.management.commands.review_hub_recipe_sheet import HEADERS


COLUMNS = [
    "المنتج", "القسم", "حجم/أساس التقديم", "مكوّن المخزون",
    "الحد الأدنى", "الحد الأعلى", "الوحدة", "حصص الدفعة",
    "اعتماد المقدار", "المصدر", "ملاحظة التحضير/التكلفة",
    "وضع خصم المخزون",
]


def row(**overrides):
    base = {
        "المنتج": "قهوة عربية",
        "القسم": "ساخنة",
        "حجم/أساس التقديم": "فنجان",
        "مكوّن المخزون": "بن عربي",
        "الحد الأدنى": "10",
        "الحد الأعلى": "10",
        "الوحدة": "g",
        "حصص الدفعة": "1",
        "اعتماد المقدار": "معتمد مبدئياً",
        "المصدر": "قرار مدير هَبّ",
        "ملاحظة التحضير/التكلفة": "",
        "وضع خصم المخزون": "غير مفعّل",
    }
    base.update(overrides)
    return base


class ReviewedHubRecipeSheetTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name_ar="مشروبات", name_en="Drinks")
        self.product = Product.objects.create(
            category=cat, name_ar="قهوة عربية", price_syp=250,
        )
        self.inventory = InventoryItem.objects.create(
            name_ar="بن عربي", code="COFFEE-AR",
            unit=InventoryItem.Unit.KG,
            current_quantity=Decimal("7.000"),
            estimated_unit_cost_syp=Decimal("500.00"),
        )
        self.recipe = ProductRecipeItem.objects.create(
            product=self.product, inventory_item=self.inventory,
            unit=InventoryItem.Unit.KG,
            quantity_per_unit=Decimal("0.012"), is_active=False,
        )

    def preview(self, rows, *, headers=None):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "recipe-export.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers or COLUMNS)
                writer.writeheader()
                for item in rows:
                    writer.writerow(item)
            output = StringIO()
            call_command("review_hub_recipe_sheet", "--file", str(path), "--json", stdout=output)
            return json.loads(output.getvalue())

    def test_exact_g_to_kg_match_is_preview_only(self):
        report = self.preview([row()])
        self.assertTrue(report["read_only"])
        self.assertEqual(report["counts"], {"review_candidate": 1})
        result = report["rows"][0]
        self.assertEqual(result["proposed_quantity"], "0.010")
        self.assertEqual(result["current_quantity"], "0.012")
        self.assertFalse(result["current_recipe_active"])
        self.recipe.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(self.recipe.quantity_per_unit, Decimal("0.012"))
        self.assertFalse(self.recipe.is_active)
        self.assertEqual(self.inventory.current_quantity, Decimal("7.000"))
        self.assertEqual(self.inventory.estimated_unit_cost_syp, Decimal("500.00"))

    def test_proposals_ranges_offered_sugar_and_batches_require_review(self):
        report = self.preview([
            row(**{"اعتماد المقدار": "تجربة"}),
            row(**{"الحد الأدنى": "10", "الحد الأعلى": "12"}),
            row(**{"مكوّن المخزون": "سكر مقدّم"}),
            row(**{"حصص الدفعة": "25"}),
        ])
        self.assertEqual(report["counts"], {
            "needs_batch_review": 1,
            "needs_measurement": 1,
            "provisional": 1,
            "requires_consumption_policy": 1,
        })
        self.assertEqual(ProductRecipeItem.objects.count(), 1)

    def test_sachets_and_unmapped_ingredients_are_never_assumed(self):
        report = self.preview([
            row(**{"الوحدة": "ظرف"}),
            row(**{"مكوّن المخزون": "لا يوجد هذا المكون"}),
        ])
        self.assertEqual(report["counts"], {
            "needs_inventory_mapping": 1, "needs_unit_mapping": 1,
        })

    def test_no_activate_or_apply_option(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "recipe-export.csv"
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerow(row())
            with self.assertRaises(CommandError):
                call_command("review_hub_recipe_sheet", "--file", str(path), "--apply")

    def test_unknown_header_refused(self):
        self.assertIn("المنتج", HEADERS)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("product,ingredient\nx,y\n", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("review_hub_recipe_sheet", "--file", str(path))
