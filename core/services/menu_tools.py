"""Safe product bulk-edit actions for the staff menu tools workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from catalog.models import MenuSection, PrepStation, Tag
from core.models import ActivityLog, Product
from core.services.bulk_edit import BulkEditValidationError, ObjectChange, validate_selected_objects


@dataclass(frozen=True)
class ProductBulkAction:
    code: str
    label: str
    value: Any = None
    target_label: str = ""


@dataclass(frozen=True)
class ProductBulkResult:
    action: ProductBulkAction
    requested_count: int
    matched_count: int
    changes: list[ObjectChange]
    saved: bool = False
    activity_log_ids: list[int] | None = None


BOOLEAN_ACTIONS = {
    "mark_available": ("تحديد كمتاح", "is_available", True),
    "mark_unavailable": ("تحديد كغير متاح", "is_available", False),
    "show_on_qr": ("إظهار على QR", "visible_on_qr", True),
    "hide_from_qr": ("إخفاء من QR", "visible_on_qr", False),
    "enable_qr_ordering": ("تفعيل الطلب عبر QR", "orderable_on_qr", True),
    "disable_qr_ordering": ("تعطيل الطلب عبر QR", "orderable_on_qr", False),
}
PRICE_ACTIONS = {
    "set_exact_price": "تعيين سعر محدد",
    "increase_price_fixed": "زيادة السعر بمبلغ ثابت",
    "decrease_price_fixed": "تخفيض السعر بمبلغ ثابت",
}
SECTION_ACTIONS = {
    "move_to_menu_section": "نقل إلى قسم منيو",
    "add_to_menu_section": "إضافة إلى قسم منيو",
    "remove_from_menu_section": "إزالة من قسم منيو",
}
PREP_ACTIONS = {"set_prep_station": "تعيين محطة التحضير"}
TAG_ACTIONS = {"add_tag": "إضافة وسم", "remove_tag": "إزالة وسم"}
ALLOWED_ACTIONS = {
    **{code: spec[0] for code, spec in BOOLEAN_ACTIONS.items()},
    **PRICE_ACTIONS,
    **SECTION_ACTIONS,
    **PREP_ACTIONS,
    **TAG_ACTIONS,
}


def resolve_product_bulk_action(action_code: str, raw_value: str = "") -> ProductBulkAction:
    action_code = (action_code or "").strip()
    raw_value = (raw_value or "").strip()
    if action_code not in ALLOWED_ACTIONS:
        raise BulkEditValidationError("اختر إجراءً جماعياً صالحاً.")

    if action_code in BOOLEAN_ACTIONS:
        label, _field_name, value = BOOLEAN_ACTIONS[action_code]
        return ProductBulkAction(action_code, label, value)

    if action_code in PRICE_ACTIONS:
        try:
            amount = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise BulkEditValidationError("أدخل قيمة سعر صحيحة بالليرة السورية.") from exc
        if amount < 0:
            raise BulkEditValidationError("لا يمكن استخدام قيمة سعر سالبة.")
        if action_code in {"increase_price_fixed", "decrease_price_fixed"} and amount == 0:
            raise BulkEditValidationError("أدخل مبلغاً أكبر من صفر لتعديل السعر.")
        return ProductBulkAction(action_code, PRICE_ACTIONS[action_code], amount, f"{amount} ل.س")

    if action_code in SECTION_ACTIONS:
        section = _get_menu_section(raw_value)
        return ProductBulkAction(action_code, SECTION_ACTIONS[action_code], section.pk, section.name_ar)

    if action_code in PREP_ACTIONS:
        station = _get_prep_station(raw_value)
        return ProductBulkAction(action_code, PREP_ACTIONS[action_code], station.pk, station.name_ar)

    if action_code in TAG_ACTIONS:
        tag = _get_tag(raw_value)
        return ProductBulkAction(action_code, TAG_ACTIONS[action_code], tag.pk, tag.name_ar)

    raise BulkEditValidationError("الإجراء الجماعي غير مدعوم.")


def preview_product_bulk_action(
    selected_identifiers: Sequence[Any],
    action_code: str,
    raw_value: str = "",
    *,
    base_queryset=None,
) -> ProductBulkResult:
    action = resolve_product_bulk_action(action_code, raw_value)
    products = validate_selected_objects(Product, selected_identifiers, base_queryset=base_queryset)
    changes = [_preview_product_change(product, action) for product in products]
    return ProductBulkResult(
        action=action,
        requested_count=len(selected_identifiers),
        matched_count=len(products),
        changes=changes,
        saved=False,
        activity_log_ids=[],
    )


def apply_product_bulk_action(
    selected_identifiers: Sequence[Any],
    action_code: str,
    raw_value: str = "",
    *,
    actor=None,
    base_queryset=None,
) -> ProductBulkResult:
    action = resolve_product_bulk_action(action_code, raw_value)
    with transaction.atomic():
        queryset = (base_queryset if base_queryset is not None else Product.objects.all()).select_for_update()
        products = validate_selected_objects(Product, selected_identifiers, base_queryset=queryset)
        changes: list[ObjectChange] = []
        activity_log_ids: list[int] = []
        for product in products:
            change = _preview_product_change(product, action)
            _apply_product_change(product, action)
            log = ActivityLog.objects.create(
                actor=actor if getattr(actor, "is_authenticated", True) else None,
                action="staff_menu_tools_bulk_edit",
                details={
                    "model": Product._meta.label,
                    "object_pk": _json_safe(product.pk),
                    "object_identifier": _json_safe(product.public_code),
                    "bulk_action": action.code,
                    "bulk_action_value": _json_safe(action.value),
                    "changes": change.changes,
                },
            )
            changes.append(change)
            activity_log_ids.append(log.pk)
    return ProductBulkResult(action, len(selected_identifiers), len(products), changes, True, activity_log_ids)


def _preview_product_change(product: Product, action: ProductBulkAction) -> ObjectChange:
    before = _state_for_action(product, action)
    after = _after_state(product, action, before)
    changes = {
        field: {"before": _json_safe(before[field]), "after": _json_safe(after[field])}
        for field in after
        if before.get(field) != after[field]
    }
    return ObjectChange(
        object_pk=_json_safe(product.pk),
        object_label=str(product),
        object_identifier=_json_safe(product.public_code),
        changes=changes,
    )


def _state_for_action(product: Product, action: ProductBulkAction) -> dict[str, Any]:
    if action.code in BOOLEAN_ACTIONS:
        _label, field_name, _value = BOOLEAN_ACTIONS[action.code]
        return {field_name: getattr(product, field_name)}
    if action.code in PRICE_ACTIONS:
        return {"price_syp": product.price_syp}
    if action.code in SECTION_ACTIONS:
        return {"menu_sections": list(product.menu_sections.order_by("sort_order", "name_ar").values_list("name_ar", flat=True))}
    if action.code in PREP_ACTIONS:
        return {"prep_station_ref": product.prep_station_ref.name_ar if product.prep_station_ref_id else "—"}
    if action.code in TAG_ACTIONS:
        return {"tags": list(product.tags.order_by("name_ar").values_list("name_ar", flat=True))}
    return {}


def _after_state(product: Product, action: ProductBulkAction, before: dict[str, Any]) -> dict[str, Any]:
    if action.code in BOOLEAN_ACTIONS:
        _label, field_name, value = BOOLEAN_ACTIONS[action.code]
        return {field_name: value}
    if action.code == "set_exact_price":
        return {"price_syp": action.value}
    if action.code == "increase_price_fixed":
        return {"price_syp": product.price_syp + action.value}
    if action.code == "decrease_price_fixed":
        return {"price_syp": max(product.price_syp - action.value, 0)}
    if action.code == "move_to_menu_section":
        return {"menu_sections": [action.target_label]}
    if action.code == "add_to_menu_section":
        sections = list(before["menu_sections"])
        if action.target_label not in sections:
            sections.append(action.target_label)
        return {"menu_sections": sections}
    if action.code == "remove_from_menu_section":
        return {"menu_sections": [name for name in before["menu_sections"] if name != action.target_label]}
    if action.code == "set_prep_station":
        return {"prep_station_ref": action.target_label}
    if action.code == "add_tag":
        tags = list(before["tags"])
        if action.target_label not in tags:
            tags.append(action.target_label)
        return {"tags": tags}
    if action.code == "remove_tag":
        return {"tags": [name for name in before["tags"] if name != action.target_label]}
    return before


def _apply_product_change(product: Product, action: ProductBulkAction) -> None:
    if action.code in BOOLEAN_ACTIONS:
        _label, field_name, value = BOOLEAN_ACTIONS[action.code]
        setattr(product, field_name, value)
        product.full_clean()
        product.save(update_fields=[field_name, "updated_at"])
        return
    if action.code in PRICE_ACTIONS:
        product.price_syp = _after_state(product, action, {})["price_syp"]
        product.full_clean()
        product.save(update_fields=["price_syp", "updated_at"])
        return
    if action.code == "move_to_menu_section":
        product.menu_sections.set([action.value])
        return
    if action.code == "add_to_menu_section":
        product.menu_sections.add(action.value)
        return
    if action.code == "remove_from_menu_section":
        product.menu_sections.remove(action.value)
        return
    if action.code == "set_prep_station":
        product.prep_station_ref_id = action.value
        product.full_clean()
        product.save(update_fields=["prep_station_ref", "updated_at"])
        return
    if action.code == "add_tag":
        product.tags.add(action.value)
        return
    if action.code == "remove_tag":
        product.tags.remove(action.value)
        return


def _get_menu_section(raw_pk: str) -> MenuSection:
    try:
        return MenuSection.objects.get(pk=int(raw_pk), is_active=True)
    except (TypeError, ValueError, MenuSection.DoesNotExist) as exc:
        raise BulkEditValidationError("اختر قسم منيو صالحاً.") from exc


def _get_prep_station(raw_pk: str) -> PrepStation:
    try:
        return PrepStation.objects.get(pk=int(raw_pk), is_active=True)
    except (TypeError, ValueError, PrepStation.DoesNotExist) as exc:
        raise BulkEditValidationError("اختر محطة تحضير صالحة.") from exc


def _get_tag(raw_pk: str) -> Tag:
    try:
        return Tag.objects.get(pk=int(raw_pk), is_active=True)
    except (TypeError, ValueError, Tag.DoesNotExist) as exc:
        raise BulkEditValidationError("اختر وسماً صالحاً.") from exc


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
