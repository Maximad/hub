"""Price the four newly approved Hub sale products, without touching stock.

--template outputs a reviewable CSV; --file is preview-only unless --apply
with a named active staff actor. The batch is atomic and optimistic-locks
existing sale prices. New sale prices must be owner-reviewed; no guessed prices.
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ActivityLog, Product


APPROVED = {
    "sahleb": "سحلب",
    "tea_bag": "شاي ظرف",
    "tea_khamir_glass": "شاي خمير - كأس",
    "arak_large_glass": "عرق الريان - كأس كبير",
}
FIELDS = ("hub_decision_key", "name_ar", "expected_price_syp", "new_price_syp", "publish")
LOG_ACTION = "hub_new_product_price_approval_2026_10"


def _product_for_key(key, *, lock=False):
    query = Product.objects
    if lock:
        query = query.select_for_update()
    matches = list(query.filter(metadata__hub_decision_key=key)[:2])
    if len(matches) != 1 or matches[0].name_ar != APPROVED[key]:
        raise CommandError(f"Missing/ambiguous product identity for {key}; do not guess IDs")
    return matches[0]


def _non_negative_int(raw, label, *, minimum=0):
    value = str(raw).strip()
    if not value.isascii() or not value.isdecimal():
        raise CommandError(f"{label}: expected integer new-SYP amount")
    result = int(value)
    if not minimum <= result <= 1_000_000:
        raise CommandError(f"{label}: amount out of approved validation range")
    return result


def _load_rows(path):
    if not path.is_file():
        raise CommandError(f"Missing CSV: {path}")
    if path.stat().st_size > 50_000:
        raise CommandError("Price batch CSV unexpectedly large")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or ()) != FIELDS:
                raise CommandError("Incorrect CSV headers; generate with --template")
            rows = []
            seen = set()
            for n, record in enumerate(reader, start=2):
                if None in record or any(v is None for v in record.values()):
                    raise CommandError(f"Row {n}: incomplete/extra columns")
                key = record["hub_decision_key"].strip()
                if key not in APPROVED or key in seen:
                    raise CommandError(f"Row {n}: unknown or duplicate product key")
                seen.add(key)
                if record["name_ar"].strip() != APPROVED[key]:
                    raise CommandError(f"Row {n}: product name mismatch")
                if record["publish"].strip() not in {"yes", "no"}:
                    raise CommandError(f"Row {n}: publish must be exactly 'yes' or 'no'")
                rows.append({
                    "key": key,
                    "name_ar": APPROVED[key],
                    "expected_price_syp": _non_negative_int(
                        record["expected_price_syp"], f"Row {n}: expected_price_syp",
                    ),
                    "new_price_syp": _non_negative_int(
                        record["new_price_syp"], f"Row {n}: new_price_syp", minimum=1,
                    ),
                    "publish": record["publish"].strip() == "yes",
                })
    except (UnicodeError, csv.Error, OSError) as exc:
        raise CommandError(f"Unable to read price CSV: {exc}") from exc
    if not rows:
        raise CommandError("CSV has no approved price rows")
    return rows


def _csv_template():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(FIELDS)
    for key, name in APPROVED.items():
        product = _product_for_key(key)
        writer.writerow([key, name, product.price_syp, "", "no"])
    return output.getvalue()


def _apply_row(row, actor):
    product = _product_for_key(row["key"], lock=True)
    expected = row["expected_price_syp"]
    new = row["new_price_syp"]
    publish = row["publish"]

    old = {
        "price_syp": product.price_syp,
        "is_available": product.is_available,
        "visible_on_qr": product.visible_on_qr,
        "orderable_on_qr": product.orderable_on_qr,
        "visible_on_pos": product.visible_on_pos,
        "orderable_on_pos": product.orderable_on_pos,
    }
    # A priced item already on sale must not be hidden or repriced by this
    # one-time onboarding workflow. The ordinary catalog editor handles that.
    was_published = any(old[field] for field in (
        "is_available", "visible_on_qr", "orderable_on_qr",
        "visible_on_pos", "orderable_on_pos",
    ))
    if was_published and not publish:
        raise CommandError(f"{row['key']}: already visible/orderable; refusing to unpublish")

    fully_matches = (
        old["price_syp"] == new
        and all(old[field] == publish for field in (
            "is_available", "visible_on_qr", "orderable_on_qr",
            "visible_on_pos", "orderable_on_pos",
        ))
    )
    new_status = "published" if publish else "priced_pending_publish"
    current_metadata = dict(product.metadata or {})
    if fully_matches and current_metadata.get("status") == new_status:
        return {"key": row["key"], "status": "unchanged", "price_syp": new,
                "publish": publish, "product_id": product.pk}

    if product.price_syp != expected and not fully_matches:
        raise CommandError(
            f"{row['key']}: stale price (expected {expected}, now {product.price_syp}); "
            "regenerate --template before changing it"
        )
    if was_published and product.price_syp != new:
        raise CommandError(
            f"{row['key']}: already live with different price; manual repricing required"
        )
    if not fully_matches and old["price_syp"] == new and old["is_available"] != publish:
        if was_published and not publish:
            raise CommandError(f"{row['key']}: refusing to unpublish")
    product.price_syp = new
    product.is_available = publish
    product.visible_on_qr = publish
    product.orderable_on_qr = publish
    current_metadata["status"] = new_status
    product.metadata = current_metadata
    # Product pre_save synchronizes legacy POS flags from the QR/menu fields.
    # Persist both sets or pre_save would only change in-memory flag values.
    product.save(update_fields=[
        "price_syp", "is_available", "visible_on_qr", "orderable_on_qr",
        "visible_on_pos", "orderable_on_pos", "metadata", "updated_at",
    ])
    product.refresh_from_db()
    if product.price_syp != new or any(
        getattr(product, flag) != publish for flag in (
            "is_available", "visible_on_qr", "orderable_on_qr",
            "visible_on_pos", "orderable_on_pos",
        )
    ):
        raise CommandError(f"{row['key']}: persisted channel verification failed; rollback")
    ActivityLog.objects.create(
        actor=actor, action=LOG_ACTION,
        details={
            "key": row["key"], "product_id": product.pk,
            "previous": old, "new_price_syp": new, "published": publish,
        },
    )
    return {"key": row["key"], "product_id": product.pk,
            "status": "updated", "previous_price_syp": old["price_syp"],
            "price_syp": new, "publish": publish}


class Command(BaseCommand):
    help = "Review or explicitly apply owner-provided prices to the four new Hub products."

    def add_arguments(self, parser):
        parser.add_argument("--template", action="store_true", help="Print fill-in CSV template")
        parser.add_argument("--file", type=Path, help="Reviewed CSV path inside the Django container")
        parser.add_argument("--apply", action="store_true", help="Commit approved prices atomically")
        parser.add_argument("--actor", help="Active Django staff username for applied changes")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        if options["template"]:
            if options["file"] or options["apply"] or options["actor"] or options["as_json"]:
                raise CommandError("--template cannot be combined with other options")
            self.stdout.write(_csv_template(), ending="")
            return
        if not options["file"]:
            raise CommandError("Supply --template or --file <reviewed.csv>")
        if options["apply"] and not options["actor"]:
            raise CommandError("--apply requires --actor")
        actor = None
        if options.get("actor"):
            actor = get_user_model().objects.filter(
                username=options["actor"], is_active=True, is_staff=True,
            ).first()
            if actor is None:
                raise CommandError("Actor must be an existing active Django staff user")
        rows = _load_rows(options["file"])
        with transaction.atomic():
            results = [_apply_row(row, actor) for row in rows]
            if not options["apply"]:
                transaction.set_rollback(True)
        payload = {
            "read_only": not options["apply"],
            "applied": bool(options["apply"]),
            "counts": dict(Counter(row["status"] for row in results)),
            "products": results,
            "warnings": [
                "All stock quantities and recipe active flags are unchanged.",
                "publish=yes explicitly exposes a new product in both menu and staff POS.",
                "This is an operational sale price only; no accounting valuation or payment is posted.",
            ],
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write("APPLIED" if options["apply"] else "PREVIEW ONLY: transaction rolled back")
            for result in results:
                self.stdout.write(
                    f"{result['status']}: {result['key']} product={result['product_id']} "
                    f"price={result['price_syp']} publish={result['publish']}"
                )
            self.stdout.write("Counts: " + json.dumps(payload["counts"], ensure_ascii=False))
