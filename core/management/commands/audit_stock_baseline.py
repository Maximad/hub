"""Read-only operating-stock baseline, before recipe imports or forecasting.

This command never modifies inventory, prices, recipes, purchases, tasks,
orders, or financial records. It distinguishes historical aggregate imports
from per-order served sales and does not treat a zero balance as verified stock.
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.models import (
    InventoryItem, Order, OrderItem, Product, ProductRecipeItem, StockMovement,
)
from core.services.inventory_provenance import quantity_provenance

HISTORICAL_IMPORT_MARKER = "import_key="
HISTORICAL_ORDER_MARKER = "[PRE_SYSTEM_HISTORICAL_SALES]"


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


def build_stock_baseline(*, limit=20, days=28, include_pilot=False):
    if not (1 <= limit <= 100):
        raise ValueError("limit must be between 1 and 100")
    if include_pilot and not (10 <= limit <= 20):
        raise ValueError("pilot limit must be between 10 and 20")
    if not (1 <= days <= 365):
        raise ValueError("days must be between 1 and 365")

    now = timezone.now()
    items = InventoryItem.objects.all()
    products = Product.objects.all()
    recipes = ProductRecipeItem.objects.all()

    served = _served_lines()
    # Imports may be marked at the line OR order level. Unmarked historic
    # aggregates remain an uncertainty, not certified live sales.
    legacy_marker = Q(item_note__contains=HISTORICAL_IMPORT_MARKER) | Q(
        order__notes__contains=HISTORICAL_ORDER_MARKER
    )
    legacy = served.filter(legacy_marker)
    # This is operational served-order evidence, not proof of settled payment.
    operational = served.exclude(legacy_marker)
    recent = operational.filter(created_at__gte=now - timedelta(days=days))
    recent_by_product = _sales_by_product(recent)
    lifetime_by_product = _sales_by_product(operational)
    aggregate_by_product = _sales_by_product(legacy)

    review_rows = []
    physical_candidates = []
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

        # A stock/recipe pilot concerns drinks and food, not time-based services,
        # tickets or memberships. Legacy OTHER + food/beverage needs human
        # confirmation; availability by itself is not evidence of sales.
        if (
            product.product_type in (Product.ProductType.FOOD, Product.ProductType.DRINK)
            or (
                product.product_type == Product.ProductType.OTHER
                and product.item_type in (Product.ItemType.FOOD, Product.ItemType.BEVERAGE)
            )
        ):
            physical_candidates.append((product, review_rows[-1]))

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
    if include_pilot:
        # No legacy-aggregate volume in pilot ranking, regardless of its size.
        # Where operational sales evidence is absent, candidates are explicitly
        # labelled a catalogue fallback, not claimed as bestsellers.
        physical_candidates.sort(key=lambda pair: (
            -pair[1]["served_line_units_in_window"],
            -pair[1]["served_line_units_lifetime_excluding_legacy"],
            -int(bool(pair[0].price_syp) and (
                pair[0].orderable_on_pos or pair[0].orderable_on_qr)),
            -int(bool(pair[0].recipe_items.all())),
            pair[0].pk,
        ))
        selected = physical_candidates[:limit]
        materials = {
            recipe.inventory_item_id: recipe.inventory_item
            for product, row in selected for recipe in product.recipe_items.all()
        }
        provenance = quantity_provenance(materials.values())
        pilot_rows = []
        for product, row in selected:
            reasons = []
            if row["served_line_units_in_window"]:
                evidence = "recent_served_order_lines_not_payment_proof"
            elif row["served_line_units_lifetime_excluding_legacy"]:
                evidence = "older_served_order_lines_not_payment_proof"
            elif row["legacy_import_served_units"]:
                evidence = "historical_import_only_not_demand_proof"
            else:
                evidence = "catalogue_fallback_no_sales_evidence"
            if product.price_syp <= 0:
                reasons.append("review_unpriced_product")
            if not (
                (product.visible_on_pos and product.orderable_on_pos)
                or (product.visible_on_qr and product.orderable_on_qr)
            ):
                reasons.append("review_sales_visibility")
            if product.product_type == Product.ProductType.OTHER:
                reasons.append("verify_legacy_product_classification")
            recipes = list(product.recipe_items.all())
            if not recipes:
                reasons.append("map_recipe_ingredients")
            elif not any(recipe.is_active for recipe in recipes):
                reasons.append("review_inactive_recipe_not_auto_activate")
            ingredients = []
            for recipe in recipes:
                ingredient = recipe.inventory_item
                count = provenance.get(ingredient.pk, {"state": "no_snapshot"})
                component_issues = []
                if not ingredient.code:
                    component_issues.append("map_missing_sku")
                if not ingredient.is_active:
                    component_issues.append("review_inactive_ingredient")
                if recipe.unit != ingredient.unit:
                    component_issues.append("review_unit_mismatch")
                if count["state"] == "no_snapshot":
                    component_issues.append("physical_count_required")
                elif count["state"] != "matches_count":
                    component_issues.append("review_count_provenance")
                if ingredient.estimated_unit_cost_syp is None:
                    component_issues.append("missing_reference_cost")
                # Non-null estimates are NOT evidence of a supplier invoice.
                component_issues.append("invoice_cost_not_verified")
                ingredients.append({
                    "inventory_id": ingredient.pk,
                    "inventory_code": ingredient.code,
                    "inventory_name": ingredient.name_ar,
                    "inventory_unit": ingredient.unit,
                    "recipe_unit": recipe.unit,
                    "quantity_per_unit": str(recipe.quantity_per_unit),
                    "recipe_active": recipe.is_active,
                    "count_provenance": count["state"],
                    "count_id": count.get("count_id"),
                    "reference_cost_present": ingredient.estimated_unit_cost_syp is not None,
                    "issues": component_issues,
                })
            pilot_rows.append({
                "product_id": product.pk,
                "product_public_code": str(product.public_code),
                "product": product.name_ar,
                "product_type": product.product_type,
                "configured_sale_price_syp": product.price_syp,
                "sales_evidence": evidence,
                "served_units_in_window": row["served_line_units_in_window"],
                "served_units_lifetime_excluding_legacy": row["served_line_units_lifetime_excluding_legacy"],
                "legacy_aggregate_units_excluded_from_ranking": row["legacy_import_served_units"],
                "review_reasons": reasons,
                "ingredients": ingredients,
                "pilot_approved": False,
            })
        report["pilot_products"] = pilot_rows
        report["pilot_selection"] = {
            "eligible_active_food_drink_products": len(physical_candidates),
            "selected": len(pilot_rows),
            "limit": limit,
            "order": "recent served operational units, older served units, catalogued availability/recipe, product ID",
            "legacy_imports_ranked_as_sales": False,
            "approval_state": "proposed_for_staff_review_not_approved",
            "data_source": "current database at command execution; not a live production export from GitHub",
        }
        report["cautions"].extend([
            "The list is a review queue, not verified top sellers; unmarked historic imports may remain.",
            "Stock count provenance requires review after subsequent movements; estimates are not invoice-backed costs.",
            "No recipe, stock movement, purchasing, sale price or financial posting is approved by this pilot.",
        ])
    return report


class Command(BaseCommand):
    help = "Read-only stock baseline and product-review priority report; no inventory/finance changes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--days", type=int, default=28)
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--pilot", action="store_true", help="Explainable 10-20-product food/drink pilot; read-only")

    def handle(self, *args, **options):
        try:
            report = build_stock_baseline(limit=options["limit"], days=options["days"], include_pilot=options["pilot"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return
        summary = report["summary"]
        self.stdout.write("Hub stock baseline (READ ONLY)")
        for key, value in summary.items():
            self.stdout.write(f"{key}={value}")
        if options["pilot"]:
            selection = report["pilot_selection"]
            self.stdout.write(f"Pilot proposals: {selection['selected']} of {selection['eligible_active_food_drink_products']} eligible food/drink products")
            for row in report["pilot_products"]:
                self.stdout.write(
                    f"  {row['product_id']}: {row['product']} "
                    f"served_{report['window_days']}d={row['served_units_in_window']} "
                    f"evidence={row['sales_evidence']} "
                    f"mapped_ingredients={len(row['ingredients'])} "
                    f"review={','.join(row['review_reasons']) or 'inspect_component_issues'}"
                )
            self.stdout.write("No database changes made. Requires physical counts and source verification.")
            return
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
