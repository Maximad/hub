"""Read-only operating-stock baseline, before recipe imports or forecasting.

This command never modifies inventory, prices, recipes, purchases, tasks,
orders, or financial records. It distinguishes historical aggregate imports
from per-order served sales and does not treat a zero balance as verified stock.
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.utils import timezone

from core.models import (
    InventoryItem, Order, OrderItem, Product, ProductRecipeItem, StockMovement,
)

HISTORICAL_IMPORT_MARKER = "import_key="


def _served_lines():
    return (
        OrderItem.objects.filter(order__status=Order.Status.SERVED)
        .exclude(prep_status=OrderItem.PrepStatus.CANCELLED)
    )


def _sales_by_product(queryset):
    data = (
        queryset.values("product_id")
        .annotate(units=Sum("quantity"), lines=Count("pk"))
        .order_by()
    )
    return {
        item["product_id"]: {"units": int(item["units"] or 0), "lines": int(item["lines"])}
        for item in data
    }


def build_stock_baseline(*, limit=20, days=28):
    if not (1 <= limit <= 100):
        raise ValueError("limit must be between 1 and 100")
    if not (1 <= days <= 365):
        raise ValueError("days must be between 1 and 365")

    now = timezone.now()
    items = InventoryItem.objects.all()
    products = Product.objects.all()
    recipes = ProductRecipeItem.objects.all()

    served = _served_lines()
    legacy = served.filter(item_note__contains=HISTORICAL_IMPORT_MARKER)
    # This is operational served-order evidence, not proof of settled payment.
    operational = served.exclude(item_note__contains=HISTORICAL_IMPORT_MARKER)
    recent = operational.filter(created_at__gte=now - timedelta(days=days))
    recent_by_product = _sales_by_product(recent)
    lifetime_by_product = _sales_by_product(operational)
    aggregate_by_product = _sales_by_product(legacy)

    review_rows = []
    for product in (
        products.filter(is_available=True)
        .prefetch_related("recipe_items__inventory_item")
        .order_by("pk")
    ):
        product_recipes = list(product.recipe_items.all())
        active_recipes = [r for r in product_recipes if r.is_active]
        inactive_recipes = [r for r in product_recipes if not r.is_active]
        ingredient_rows = []
        for recipe in product_recipes:
            material = recipe.inventory_item
            ingredient_rows.append({
                "inventory_id": material.pk,
                "inventory_code": material.code,
                "name": material.name_ar,
                "recipe_active": recipe.is_active,
                "quantity_per_unit": str(recipe.quantity_per_unit),
                "unit": recipe.unit,
                "recorded_zero_quantity": material.current_quantity == 0,
                "missing_reference_cost": material.estimated_unit_cost_syp is None,
            })
        recent_sales = recent_by_product.get(product.pk, {})
        historic_sales = lifetime_by_product.get(product.pk, {})
        aggregated = aggregate_by_product.get(product.pk, {})
        review_rows.append({
            "product_id": product.pk,
            "product": product.name_ar,
            "served_line_units_in_window": recent_sales.get("units", 0),
            "served_line_units_lifetime_excluding_legacy": historic_sales.get("units", 0),
            "legacy_import_served_units": aggregated.get("units", 0),
            "recipe_lines_active": len(active_recipes),
            "recipe_lines_inactive": len(inactive_recipes),
            "ingredients_with_zero_recorded_balance": sum(x["recorded_zero_quantity"] for x in ingredient_rows),
            "ingredients_missing_reference_cost": sum(x["missing_reference_cost"] for x in ingredient_rows),
            "ingredients": ingredient_rows,
            "readiness": (
                "has_active_recipe_requires_inventory_verification" if active_recipes
                else "inactive_recipe_needs_review" if inactive_recipes
                else "no_recipe"
            ),
        })

    review_rows.sort(
        key=lambda x: (
            -x["served_line_units_in_window"],
            -x["served_line_units_lifetime_excluding_legacy"],
            -x["legacy_import_served_units"],
            x["product"],
            x["product_id"],
        )
    )
    report = {
        "read_only": True,
        "snapshot_at": now.isoformat(),
        "window_days": days,
        "forecast_ready": False,
        "cautions": [
            "A recorded zero is not proof of a physical stockout or completed stock count.",
            "Served order items are operational evidence; do not infer payment completion.",
            "Legacy monthly aggregate imports are excluded from recent per-order demand.",
            "Inactive recipes are not eligible for stock deduction or confident forecasts.",
            "Do not post physical counts, consumption or prices from this report.",
        ],
        "summary": {
            "products": products.count(),
            "available_products": products.filter(is_available=True).count(),
            "inventory_items": items.count(),
            "active_inventory_items": items.filter(is_active=True).count(),
            "recorded_zero_balances": items.filter(current_quantity=0).count(),
            "missing_reference_cost": items.filter(estimated_unit_cost_syp__isnull=True).count(),
            "recipe_lines": recipes.count(),
            "active_recipe_lines": recipes.filter(is_active=True).count(),
            "available_products_without_active_recipes": sum(not r["recipe_lines_active"] for r in review_rows),
            "stock_movement_records": StockMovement.objects.count(),
            "opening_balance_movement_records": StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.OPENING_BALANCE,
                is_cancelled=False,
            ).count(),
            "order_lines": OrderItem.objects.count(),
            "served_operational_lines_in_window": recent.count(),
            "served_operational_units_in_window": sum(r["units"] for r in recent_by_product.values()),
            "served_legacy_aggregate_lines": legacy.count(),
            "served_legacy_aggregate_units": sum(r["units"] for r in aggregate_by_product.values()),
            "order_lines_with_stock_deducted": OrderItem.objects.filter(stock_deducted=True).count(),
        },
        "priority_products": review_rows[:limit],
        "priority_selection": "Available products ranked by served line units, then separately labeled aggregate history; not a purchase order.",
    }
    return report


class Command(BaseCommand):
    help = "Read-only stock baseline and product-review priority report; no inventory/finance changes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--days", type=int, default=28)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            report = build_stock_baseline(limit=options["limit"], days=options["days"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return
        summary = report["summary"]
        self.stdout.write("Hub stock baseline (READ ONLY)")
        for key, value in summary.items():
            self.stdout.write(f"{key}={value}")
        self.stdout.write("Priority products (not a stockout/purchase forecast):")
        for row in report["priority_products"]:
            self.stdout.write(
                f"  {row['product_id']}: {row['product']} "
                f"served_{report['window_days']}d={row['served_line_units_in_window']} "
                f"legacy_units={row['legacy_import_served_units']} "
                f"recipe_active={row['recipe_lines_active']} "
                f"recipe_inactive={row['recipe_lines_inactive']} "
                f"status={row['readiness']}"
            )
        self.stdout.write("No database changes made.")
