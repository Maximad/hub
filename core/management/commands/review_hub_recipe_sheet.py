"""Read-only preflight for the reviewed Hub Sweida recipe Google Sheet.

This command deliberately has NO --apply switch. It never writes to the database,
changes recipe activation, or updates stock, prices or finance records.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import InventoryItem, Product, ProductRecipeItem


HEADERS = {
    "المنتج", "حجم/أساس التقديم", "مكوّن المخزون",
    "الحد الأدنى", "الحد الأعلى", "الوحدة", "حصص الدفعة",
    "اعتماد المقدار",
}
STOCK_UNITS = {
    "g": "g", "غ": "g",
    "kg": "kg", "كغ": "kg",
    "ml": "ml", "مل": "ml",
    "liter": "liter", "لتر": "liter",
    "piece": "piece", "قطعة": "piece",
    "bottle": "bottle", "زجاجة": "bottle",
}
CONVERSIONS = {
    ("g", "kg"): Decimal("0.001"),
    ("kg", "g"): Decimal("1000"),
    ("ml", "liter"): Decimal("0.001"),
    ("liter", "ml"): Decimal("1000"),
}
STORAGE_PRECISION = Decimal("0.001")


def decimal_or_none(value):
    if value is None or not str(value).strip():
        return None
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def exact_unique(model, field, value):
    rows = list(model.objects.filter(**{field: value}).order_by("pk")[:2])
    return rows[0] if len(rows) == 1 else None, len(rows)


def is_provisional(label):
    return label != "معتمد مبدئياً"


def inspect_row(source, row_number):
    product_name = (source.get("المنتج") or "").strip()
    ingredient_name = (source.get("مكوّن المخزون") or "").strip()
    status = (source.get("اعتماد المقدار") or "").strip()
    result = {
        "row": row_number,
        "product": product_name,
        "ingredient": ingredient_name,
        "source_status": status,
        "finding": "",
        "reason": "",
    }

    def flag(name, reason):
        result["finding"] = name
        result["reason"] = reason
        return result

    if not product_name or not ingredient_name:
        return flag("invalid_row", "Product and ingredient labels are required.")
    if is_provisional(status):
        return flag("provisional", "Trial, suggested or otherwise unapproved quantity.")
    if "مقدّم" in ingredient_name or "مقدّم" in (source.get("ملاحظة التحضير/التكلفة") or ""):
        return flag("requires_consumption_policy", "Offered quantity is not confirmed consumed stock.")

    minimum = decimal_or_none(source.get("الحد الأدنى"))
    maximum = decimal_or_none(source.get("الحد الأعلى"))
    if minimum is None or maximum is None or minimum <= 0 or maximum <= 0:
        return flag("needs_measurement", "Exact positive quantity not recorded.")
    if minimum != maximum:
        return flag("needs_measurement", "Range must be resolved before possible activation.")

    portions = decimal_or_none(source.get("حصص الدفعة"))
    if portions is None or portions <= 0 or portions != portions.to_integral_value():
        return flag("needs_batch_review", "Valid portion count is required.")
    if portions > 1:
        return flag("needs_batch_review", "Batch yield needs an approved production/consumption model.")

    product, product_matches = exact_unique(Product, "name_ar", product_name)
    if product_matches != 1:
        return flag(
            "needs_product_mapping",
            "Missing or ambiguous exact product name; map by verified product identifier.",
        )
    ingredient, ingredient_matches = exact_unique(InventoryItem, "name_ar", ingredient_name)
    if ingredient_matches != 1:
        return flag(
            "needs_inventory_mapping",
            "Missing or ambiguous exact ingredient name; map by verified inventory code.",
        )
    if not ingredient.is_active:
        return flag("inactive_inventory", "Ingredient is not active; do not activate automatically.")

    entry_unit = STOCK_UNITS.get((source.get("الوحدة") or "").strip())
    if entry_unit is None:
        return flag("needs_unit_mapping", "Unit is not a confirmed one-to-one Django stock unit.")
    if entry_unit == ingredient.unit:
        quantity = minimum
    else:
        factor = CONVERSIONS.get((entry_unit, ingredient.unit))
        if factor is None:
            return flag("needs_unit_mapping", "Unsupported or product-specific unit conversion.")
        quantity = minimum * factor
    if quantity < STORAGE_PRECISION or quantity != quantity.quantize(STORAGE_PRECISION):
        return flag("needs_unit_precision", "Converted quantity cannot be stored losslessly at 0.001 precision.")

    existing = ProductRecipeItem.objects.filter(
        product=product, inventory_item=ingredient, unit=ingredient.unit
    ).first()
    result.update(
        product_id=product.pk,
        inventory_id=ingredient.pk,
        inventory_code=ingredient.code,
        unit=ingredient.unit,
        proposed_quantity=str(quantity),
        current_quantity=(str(existing.quantity_per_unit) if existing else None),
        current_recipe_active=(bool(existing.is_active) if existing else None),
    )
    return flag(
        "review_candidate",
        "Exact name match only. Verify stable product and inventory identifiers before any import.",
    )


def inspect_csv(path):
    if not path.is_file():
        raise CommandError("CSV file not found.")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise CommandError("CSV has no header row.")
        missing = HEADERS - set(reader.fieldnames)
        if missing:
            raise CommandError("Missing required columns: " + ", ".join(sorted(missing)))
        rows = [
            inspect_row(source, index)
            for index, source in enumerate(reader, start=2)
            if any(str(value or "").strip() for value in source.values())
        ]
    return {
        "read_only": True,
        "source": path.name,
        "rows": rows,
        "counts": dict(sorted(Counter(row["finding"] for row in rows).items())),
        "total": len(rows),
    }


class Command(BaseCommand):
    help = "Read-only audit of Hub Sweida 10_الوصفات_الموحّدة CSV; no import or stock updates."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="UTF-8 CSV export of 10_الوصفات_الموحّدة")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        report = inspect_csv(Path(options["file"]))
        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return
        self.stdout.write(f"Read-only recipe review: {report['total']} component rows")
        for finding, count in report["counts"].items():
            self.stdout.write(f"  {finding}: {count}")
        self.stdout.write("No database changes made. Identifiers and approvals are required before import.")
