"""Apply specifically approved Hub Sweida catalog/recipe decisions.

Default is a full transactional preview with rollback. --apply is explicit.
This is *not* a blanket recipe activation or inventory/cost import.
No physical stock, prices of existing products, payments, purchases or
historical orders are altered. All recipe rows stay inactive.
"""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ActivityLog, InventoryItem, Product, ProductRecipeItem


SOURCE = "hub_menu_decisions_2026_10_20"


def expected_product(pk, name):
    try:
        product = Product.objects.select_for_update().get(pk=pk)
    except Product.DoesNotExist as exc:
        raise CommandError(f"Required product #{pk} not found: {name}") from exc
    if product.name_ar != name:
        raise CommandError(f"Product #{pk} changed name: {product.name_ar!r} != {name!r}")
    return product


def expected_inventory(pk, code, unit):
    try:
        item = InventoryItem.objects.select_for_update().get(pk=pk)
    except InventoryItem.DoesNotExist as exc:
        raise CommandError(f"Required ingredient #{pk} missing: {code}") from exc
    if item.code != code:
        raise CommandError(f"Ingredient #{pk} code mismatch: {item.code!r} != {code!r}")
    if item.unit != unit:
        raise CommandError(f"Ingredient #{pk} unit mismatch: {item.unit!r} != {unit!r}")
    return item


def unique_name(model, name):
    rows = list(model.objects.filter(name_ar=name).order_by("pk")[:2])
    if len(rows) > 1:
        raise CommandError(f"Ambiguous Arabic name: {name}")
    return rows[0] if rows else None


class CatalogPlan:
    def __init__(self, actor):
        self.actor = actor
        self.events = []

    def add(self, kind, status, identifier, detail):
        self.events.append({"kind": kind, "status": status, "id": identifier, "detail": detail})
        if status != "unchanged":
            ActivityLog.objects.create(
                actor=self.actor,
                action=SOURCE,
                details={"kind": kind, "status": status, "id": str(identifier), "detail": detail},
            )

    def ingredient(self, *, code, name, unit, note, active=True):
        item = InventoryItem.objects.select_for_update().filter(code=code).first()
        if item is None:
            collision = unique_name(InventoryItem, name)
            if collision:
                raise CommandError(
                    f"Inventory name {name!r} already exists as #{collision.pk}, "
                    "map it explicitly before creating another SKU."
                )
            item = InventoryItem(
                code=code, name_ar=name, unit=unit, item_type=InventoryItem.ItemType.INGREDIENT,
                is_active=active, notes=note,
            )
            item.full_clean()
            item.save()
            self.add("inventory", "created", code, f"{name}; unit={unit}; quantity unverified")
        elif item.name_ar != name or item.unit != unit:
            raise CommandError(
                f"SKU {code} conflicts with existing item {item.name_ar!r}/{item.unit}"
            )
        else:
            self.add("inventory", "unchanged", code, "Existing SKU reused")
        return item

    def product(self, *, code, name, reference, alcoholic=False):
        by_code = list(Product.objects.select_for_update().filter(
            metadata__hub_decision_key=code
        )[:2])
        if len(by_code) > 1:
            raise CommandError(f"Multiple products with hub_decision_key={code}")
        by_name = unique_name(Product, name)
        if by_code and by_name and by_code[0].pk != by_name.pk:
            raise CommandError(f"Product key/name collision: {code} {name}")
        product = by_code[0] if by_code else by_name
        if product is None:
            product = Product(
                category=reference.category,
                name_ar=name,
                price_syp=0,
                product_type=Product.ProductType.DRINK,
                item_type=Product.ItemType.BEVERAGE,
                beverage_type=reference.beverage_type,
                requires_preparation=True,
                is_alcoholic=alcoholic,
                age_restricted=alcoholic,
                is_available=False,
                visible_on_pos=False, orderable_on_pos=False,
                visible_on_qr=False, orderable_on_qr=False,
                metadata={"hub_decision_key": code, "status": "awaiting_verified_price_and_recipe"},
            )
            product.full_clean()
            product.save()
            self.add("product", "created", code, f"{name}: hidden/non-orderable, no verified sale price")
        elif product.name_ar != name:
            raise CommandError(f"Product key {code} refers to {product.name_ar!r}, not {name!r}")
        else:
            self.add("product", "unchanged", code, f"Existing product #{product.pk} reused")
        return product

    def recipe(self, product, inventory, quantity, *, note):
        quantity = Decimal(str(quantity))
        key = {
            "product": product,
            "inventory_item": inventory,
            "unit": inventory.unit,
        }
        previous = ProductRecipeItem.objects.select_for_update().filter(**key).first()
        if previous is not None and previous.is_active:
            raise CommandError(
                f"Refusing to change active recipe #{previous.pk} for product #{product.pk}"
            )
        if previous is None:
            line = ProductRecipeItem(
                **key, quantity_per_unit=quantity,
                is_active=False, notes=note,
            )
            line.full_clean()
            line.save()
            self.add(
                "recipe", "created", f"{product.pk}:{inventory.code}",
                f"{product.name_ar} / {inventory.name_ar}: {quantity} {inventory.unit}; INACTIVE",
            )
        elif previous.quantity_per_unit != quantity or previous.notes != note:
            before = str(previous.quantity_per_unit)
            previous.quantity_per_unit = quantity
            previous.notes = note
            previous.full_clean()
            previous.save(update_fields=["quantity_per_unit", "notes", "updated_at"])
            self.add(
                "recipe", "updated", f"{product.pk}:{inventory.code}",
                f"Quantity {before} -> {quantity} {inventory.unit}; remains INACTIVE",
            )
        else:
            self.add("recipe", "unchanged", f"{product.pk}:{inventory.code}", "Already matches")

    def note_old_line(self, product, inventory, note):
        rows = ProductRecipeItem.objects.select_for_update().filter(
            product=product, inventory_item=inventory
        )
        for line in rows:
            if line.is_active:
                raise CommandError(f"Cannot supersede active recipe #{line.pk}")
            if line.notes != note:
                line.notes = note
                line.save(update_fields=["notes", "updated_at"])
                self.add(
                    "recipe_legacy_note", "updated", str(line.pk),
                    f"{product.name_ar}: retained inactive historical ingredient",
                )

    def set_available(self, product):
        if product.price_syp <= 0:
            raise CommandError("3x1 has no positive price; cannot make it available")
        changes = []
        for field in ("is_available", "visible_on_pos", "orderable_on_pos"):
            if not getattr(product, field):
                setattr(product, field, True)
                changes.append(field)
        if changes:
            product.save(update_fields=[*changes, "updated_at"])
            self.add("availability", "updated", str(product.pk), "3x1 POS sale enabled: " + ", ".join(changes))
        else:
            self.add("availability", "unchanged", str(product.pk), "Already available in POS")


def build_plan(plan):
    tea = expected_product(58, "شاي عادي")
    coffee_ar = expected_product(107, "قهوة عربية")
    espresso = expected_product(108, "قهوة إيطالية")
    cappuccino = expected_product(109, "كابوتشينو")
    arak = expected_product(84, "عرق الريان - كأس")
    three_in_one = expected_product(322, "نسكافيه 3x1")
    vodka_cocktail = expected_product(333, "كوكتيل فودكا")
    mojito = expected_product(334, "موهيتو")
    doodoo = expected_product(339, "شوت دودو")
    hookah = expected_product(362, "اركيلة")

    tea_bag = expected_inventory(175, "DRK-TEA-AHMAD-BAG", InventoryItem.Unit.PORTION)
    beans_ar = expected_inventory(173, "DRK-COF-ARABIC", InventoryItem.Unit.KG)
    beans_espresso = expected_inventory(171, "DRK-COF-ESPRESSO", InventoryItem.Unit.KG)
    milk = expected_inventory(45, "ING-DAIRY-MILK", InventoryItem.Unit.LITER)
    arak_ingredient = expected_inventory(200, "DRK-ALC-ARAK-RAYAN", InventoryItem.Unit.LITER)
    vodka = expected_inventory(201, "DRK-ALC-VODKA-SMIRNOFF", InventoryItem.Unit.LITER)
    rum = expected_inventory(206, "DRK-ALC-RUM-WHITE", InventoryItem.Unit.LITER)
    green_olives = expected_inventory(210, "ING-PICKLE-GREENOLIVE", InventoryItem.Unit.KG)
    instant_coffee = expected_inventory(172, "DRK-COF-INSTANT", InventoryItem.Unit.KG)
    tabasco = expected_inventory(125, "ING-SAUCE-TABASCO", InventoryItem.Unit.ML)
    lemon_raw = expected_inventory(5, "ING-FRT-LEMON", InventoryItem.Unit.KG)

    sahleb_sachet = plan.ingredient(
        code="DRK-SAHLEB-WIN-SACHET", name="ظرف سحلب وين",
        unit=InventoryItem.Unit.PIECE,
        note="ظرف واحد للحصة؛ الرصيد غير مجرود. تكلفة الظرف بين المصادر غير محسومة.",
    )
    milk_sachet = plan.ingredient(
        code="DRK-MILK-POWDER-SACHET", name="ظرف حليب ناشف",
        unit=InventoryItem.Unit.PIECE,
        note="ظرف واحد للسحلب؛ لا تخلطه بالحليب السائل، وراجع سعر العبوة وعدد الظروف.",
    )
    stuffed_olive = plan.ingredient(
        code="ING-PICKLE-STUFFED-OLIVE", name="زيتونة محشية",
        unit=InventoryItem.Unit.PIECE,
        note="حبة واحدة لشوت دودو؛ لا تُساوى بالزيتون الأخضر العادي.",
    )
    lemon_juice = plan.ingredient(
        code="ING-PREP-FRESH-LEMON-JUICE", name="عصير ليمون طازج",
        unit=InventoryItem.Unit.LITER,
        note=(
            "مادة تحضير مقاسة باللتر، ليست الليمون الخام المقاس بالكيلوغرام. "
            "عند العصر سجل وزن الليمون الخام وكمية العصير المنتجة فعلياً. "
            "لا تحويل ثابت غرام->مل، ولا خصم تلقائي قبل تسجيل دفعة تحضير موثقة."
        ),
    )
    three_in_one_sachet = plan.ingredient(
        code="DRK-COF-3IN1-SACHET", name="ظرف قهوة 3 في 1",
        unit=InventoryItem.Unit.PIECE,
        note="ظرف واحد للحصة؛ العلامة التجارية تثبت عند الشراء.",
    )
    plan.ingredient(
        code="DRK-HOOKAH-TOBACCO-PENDING", name="معسل أركيلة — يحتاج تحديد النكهة",
        unit=InventoryItem.Unit.KG, active=False,
        note="معسل تبغ وليس عسلاً غذائياً؛ لكل علامة/نكهة مادة منفصلة قبل خصم المخزون.",
    )

    sahleb = plan.product(code="sahleb", name="سحلب", reference=tea)
    new_tea_bag = plan.product(code="tea_bag", name="شاي ظرف", reference=tea)
    khamir = plan.product(code="tea_khamir_glass", name="شاي خمير - كأس", reference=tea)
    arak_large = plan.product(
        code="arak_large_glass", name="عرق الريان - كأس كبير", reference=arak,
        alcoholic=True,
    )
    # A cup is a sold product; no mandatory batch workflow for Shai Khamir.
    plan.add("policy", "unchanged", str(khamir.pk), "Shai Khamir sold by the glass; batch yield unverified")
    plan.add("policy", "unchanged", str(hookah.pk), "Hookah remains one sale product, flavor-specific tobacco pending")

    plan.recipe(sahleb, sahleb_sachet, "1.000", note="ظرف سحلب واحد، دون ماء محسوب")
    plan.recipe(sahleb, milk_sachet, "1.000", note="ظرف حليب ناشف واحد، دون تقدير تكلفة تلقائي")
    plan.recipe(new_tea_bag, tea_bag, "1.000", note="ظرف شاي عادي واحد لكل كأس")
    plan.recipe(arak_large, arak_ingredient, "0.075", note="75 مل عرق للكأس الكبير، وليس كاراف")

    plan.recipe(coffee_ar, beans_ar, "0.010", note="10 غ لكل فنجان، معتمد 2026-10-20")
    plan.recipe(espresso, beans_espresso, "0.016", note="16 غ لقهوة إيطالية واحدة، معتمد 2026-10-20")
    plan.recipe(cappuccino, beans_espresso, "0.018", note="18 غ لكابتشينو")
    plan.recipe(cappuccino, milk, "0.150", note="150 مل حليب لكابتشينو")
    plan.recipe(arak, arak_ingredient, "0.040", note="40 مل للكأس الصغير، بدون المساس بالكاراف")
    plan.recipe(vodka_cocktail, vodka, "0.075", note="75 مل جرعة الفودكا، المازج يحتاج اعتماداً")
    plan.recipe(mojito, vodka, "0.075", note="موهيتو هَبّ بالفودكا، لا روم؛ بقية المكونات للمراجعة")
    plan.note_old_line(
        mojito, rum,
        "سطر تاريخي غير معتمد: موهيتو هَبّ يُحضّر بالفودكا وليس بالروم. لا تفعّله.",
    )

    plan.recipe(doodoo, vodka, "0.020", note="20 مل فودكا، استثناء من جرعة الكوكتيل")
    plan.recipe(doodoo, stuffed_olive, "1.000", note="حبة زيتون محشي واحدة")
    plan.recipe(doodoo, lemon_juice, "0.020", note="20 مل عصير ليمون، إنتاجه يقاس من دفعة عصر فعلية")
    plan.note_old_line(
        doodoo, green_olives,
        "سطر تاريخي غير معتمد: شوت دودو يستخدم زيتونة محشية واحدة، لا زيتوناً أخضر عادياً.",
    )
    plan.note_old_line(
        doodoo, tabasco,
        "التوباسكو هو الشطة المعتمدة لشوت دودو: نقطتان؛ 2 مل القديمة ليست معادلة مثبتة للنقطتين. لا تفعّل.",
    )
    plan.note_old_line(
        doodoo, lemon_raw,
        "الليمون الخام بالكيلوغرام ليس 20 مل عصير؛ استخدم ING-PREP-FRESH-LEMON-JUICE بعد قياس التحضير.",
    )
    plan.note_old_line(
        three_in_one, instant_coffee,
        "سطر تاريخي غير معتمد: 3×1 يستخدم ظرفاً معبأً، لا 5 غ قهوة فورية سائبة.",
    )
    plan.recipe(
        three_in_one, three_in_one_sachet, "1.000",
        note="ظرف كامل من قهوة 3×1، اختيار العلامة التجارية في الشراء",
    )
    whiskey_products = list(Product.objects.select_for_update().filter(name_ar="كوكتيل ويسكي")[:2])
    whiskey_items = list(InventoryItem.objects.select_for_update().filter(name_ar="ويسكي")[:2])
    if len(whiskey_products) == 1 and len(whiskey_items) == 1 and whiskey_items[0].unit in {
        InventoryItem.Unit.LITER, InventoryItem.Unit.ML,
    }:
        # Stock may be stored in either liters or milliliters; no silent 1000x error.
        whiskey_qty = "0.075" if whiskey_items[0].unit == InventoryItem.Unit.LITER else "75.000"
        plan.recipe(
            whiskey_products[0], whiskey_items[0], whiskey_qty,
            note="75 مل ويسكي للكوكتيل، المازج يحتاج اعتماداً",
        )
    else:
        plan.add(
            "policy", "unchanged", "whiskey_mapping",
            "Exact whiskey catalog mapping absent/ambiguous; deferred",
        )
    plan.set_available(three_in_one)


class Command(BaseCommand):
    help = (
        "Preview or apply approved Hub Sweida product/ingredient/recipe corrections. "
        "No existing sale price, stock quantity, financial posting or recipe activation changes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Commit reviewed changes; default is rollback-only preview.")
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--actor", help="Existing active Django username for attribution.")

    def handle(self, *args, **options):
        actor = None
        if options["apply"] and not options.get("actor"):
            raise CommandError("For --apply supply --actor with an existing active Django username.")
        if options.get("actor"):
            actor = get_user_model().objects.filter(
                username=options["actor"], is_active=True,
            ).first()
            if actor is None or not actor.is_staff:
                raise CommandError("Actor must be an existing active staff user.")
        try:
            with transaction.atomic():
                plan = CatalogPlan(actor)
                build_plan(plan)
                statuses = dict(sorted(Counter(e["status"] for e in plan.events).items()))
                payload = {
                    "source": SOURCE,
                    "applied": bool(options["apply"]),
                    "read_only": not options["apply"],
                    "changes": plan.events,
                    "counts": statuses,
                    "warnings": [
                        "New sale products remain unavailable until pricing and operator review.",
                        "All revised recipes remain inactive; automatic stock deduction is not enabled.",
                        "Tabasco drops require calibration; no 2ml->2drops conversion assumed.",
                        "Raw lemon in kg and expressed juice in liters are different stock items.",
                        "No carafe deletion or other ambiguous removal performed.",
                    ],
                }
                if not options["apply"]:
                    transaction.set_rollback(True)
        except (ValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write("APPLIED" if options["apply"] else "PREVIEW ONLY: transaction rolled back")
            for event in plan.events:
                if event["status"] != "unchanged":
                    self.stdout.write(
                        f"{event['status']}: {event['kind']} {event['id']} — {event['detail']}"
                    )
            self.stdout.write("Counts: " + json.dumps(statuses, ensure_ascii=False))
            for warning in payload["warnings"]:
                self.stdout.write("NOTE: " + warning)
