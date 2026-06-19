"""Reusable bulk-edit helpers for staff/admin workflows.

The helpers in this module intentionally support safe, auditable updates only:
callers can validate a mixed list of database IDs and public codes, preview the
resulting field changes without saving, and then apply the same changes inside a
single transaction while recording before/after values in ``ActivityLog``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction

from catalog.models import ProductMedia
from core.models import ActivityLog, Product

PROTECTED_DELETE_MODELS = (Product, ProductMedia)
DEFAULT_LOOKUP_FIELDS = ("pk", "public_code", "uuid", "code")
DESTRUCTIVE_OPERATIONS = {"delete", "destroy", "remove", "purge"}


class BulkEditError(ValueError):
    """Base exception for bulk-edit service failures."""


class BulkEditValidationError(BulkEditError):
    """Raised when selection or field-change validation fails."""


class DestructiveOperationNotSupported(BulkEditError):
    """Raised when a caller attempts an unsafe destructive bulk operation."""


@dataclass(frozen=True)
class ObjectChange:
    """Before/after values for a single object in a bulk edit."""

    object_pk: Any
    object_label: str
    object_identifier: Any
    changes: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class BulkEditResult:
    """Result returned by both preview and apply helpers."""

    model_label: str
    requested_count: int
    matched_count: int
    changes: list[ObjectChange] = field(default_factory=list)
    saved: bool = False
    activity_log_ids: list[int] = field(default_factory=list)


def model_label(model: type[models.Model]) -> str:
    """Return a stable app/model label for logs and API responses."""

    return model._meta.label


def public_identifier(obj: models.Model) -> Any:
    """Return the most useful public identifier available for an object."""

    for attr in ("public_code", "uuid", "code"):
        if hasattr(obj, attr):
            return _json_safe(getattr(obj, attr))
    return obj.pk


def ensure_operation_supported(
    model: type[models.Model],
    operation: str,
    *,
    explicitly_supported: bool = False,
) -> None:
    """Refuse destructive operations unless a caller has explicitly opted in.

    ``core.Product`` and ``catalog.ProductMedia`` deletion is always rejected.
    Product media should be deactivated with ``is_active=False`` instead.
    """

    normalized = operation.lower().strip()
    is_destructive = normalized in DESTRUCTIVE_OPERATIONS
    if not is_destructive:
        return

    if model in PROTECTED_DELETE_MODELS:
        raise DestructiveOperationNotSupported(
            f"Bulk {operation!r} is not allowed for {model_label(model)}. "
            "Use a non-destructive update instead; ProductMedia should be "
            "deactivated with is_active=False."
        )
    if not explicitly_supported:
        raise DestructiveOperationNotSupported(
            f"Bulk {operation!r} for {model_label(model)} must be explicitly supported by the caller."
        )


def validate_selected_objects(
    model: type[models.Model],
    selected_identifiers: Sequence[Any],
    *,
    lookup_fields: Iterable[str] | None = None,
    base_queryset: models.QuerySet | None = None,
    allow_empty: bool = False,
) -> list[models.Model]:
    """Resolve and validate selected object IDs/public codes.

    Identifiers may be integer primary keys or public codes such as UUID-based
    ``public_code``/``uuid`` values. The returned objects preserve the request
    order and are de-duplicated by primary key.
    """

    identifiers = [
        identifier for identifier in selected_identifiers if str(identifier).strip()
    ]
    if not identifiers and not allow_empty:
        raise BulkEditValidationError("Select at least one object to bulk edit.")

    queryset = (
        base_queryset if base_queryset is not None else model._default_manager.all()
    )
    usable_lookup_fields = _usable_lookup_fields(
        model, lookup_fields or DEFAULT_LOOKUP_FIELDS
    )
    if not usable_lookup_fields:
        raise BulkEditValidationError(
            f"No usable lookup fields are available for {model_label(model)}."
        )

    resolved: list[models.Model] = []
    seen_pks: set[Any] = set()
    errors: list[str] = []
    for raw_identifier in identifiers:
        candidates = _candidate_filters(model, raw_identifier, usable_lookup_fields)
        if not candidates:
            errors.append(
                f"{raw_identifier!r} is not a valid ID or public code for {model_label(model)}."
            )
            continue

        query = models.Q()
        for candidate in candidates:
            query |= models.Q(**candidate)
        matches = list(queryset.filter(query)[:2])
        if not matches:
            errors.append(f"No {model_label(model)} object matches {raw_identifier!r}.")
            continue
        if len(matches) > 1:
            errors.append(
                f"{raw_identifier!r} matches more than one {model_label(model)} object."
            )
            continue
        obj = matches[0]
        if obj.pk in seen_pks:
            errors.append(
                f"{raw_identifier!r} selects {model_label(model)} #{obj.pk} more than once."
            )
            continue
        seen_pks.add(obj.pk)
        resolved.append(obj)

    if errors:
        raise BulkEditValidationError("; ".join(errors))
    return resolved


def preview_bulk_update(
    model: type[models.Model],
    selected_identifiers: Sequence[Any],
    changes: dict[str, Any],
    *,
    lookup_fields: Iterable[str] | None = None,
    base_queryset: models.QuerySet | None = None,
) -> BulkEditResult:
    """Preview changed values for a bulk update without saving anything."""

    ensure_operation_supported(model, "update")
    objects = validate_selected_objects(
        model,
        selected_identifiers,
        lookup_fields=lookup_fields,
        base_queryset=base_queryset,
    )
    object_changes = [_preview_object_update(obj, changes) for obj in objects]
    return BulkEditResult(
        model_label=model_label(model),
        requested_count=len(selected_identifiers),
        matched_count=len(objects),
        changes=object_changes,
        saved=False,
    )


def apply_bulk_update(
    model: type[models.Model],
    selected_identifiers: Sequence[Any],
    changes: dict[str, Any],
    *,
    actor=None,
    action: str = "bulk_edit",
    lookup_fields: Iterable[str] | None = None,
    base_queryset: models.QuerySet | None = None,
) -> BulkEditResult:
    """Apply a validated bulk update inside ``transaction.atomic()``.

    Each changed object receives an ``ActivityLog`` row containing model name,
    object identifier, and field-level before/after values.
    """

    ensure_operation_supported(model, "update")
    with transaction.atomic():
        queryset = (
            base_queryset if base_queryset is not None else model._default_manager.all()
        )
        locked_queryset = queryset.select_for_update()
        objects = validate_selected_objects(
            model,
            selected_identifiers,
            lookup_fields=lookup_fields,
            base_queryset=locked_queryset,
        )
        object_changes: list[ObjectChange] = []
        activity_log_ids: list[int] = []
        update_fields = list(changes.keys())
        for obj in objects:
            change = _preview_object_update(obj, changes)
            if change.changes:
                obj.save(update_fields=update_fields)
            log = ActivityLog.objects.create(
                actor=actor if getattr(actor, "is_authenticated", True) else None,
                action=action,
                details={
                    "model": model_label(model),
                    "object_pk": _json_safe(obj.pk),
                    "object_identifier": public_identifier(obj),
                    "changes": change.changes,
                },
            )
            object_changes.append(change)
            activity_log_ids.append(log.pk)

    return BulkEditResult(
        model_label=model_label(model),
        requested_count=len(selected_identifiers),
        matched_count=len(objects),
        changes=object_changes,
        saved=True,
        activity_log_ids=activity_log_ids,
    )


def deactivate_product_media(
    selected_identifiers: Sequence[Any],
    *,
    actor=None,
    action: str = "bulk_deactivate_product_media",
    base_queryset: models.QuerySet | None = None,
) -> BulkEditResult:
    """Safely deactivate product media instead of deleting media rows."""

    return apply_bulk_update(
        ProductMedia,
        selected_identifiers,
        {"is_active": False},
        actor=actor,
        action=action,
        base_queryset=base_queryset,
    )


def _preview_object_update(obj: models.Model, changes: dict[str, Any]) -> ObjectChange:
    _validate_changes(obj.__class__, changes)
    before_values = {
        field_name: _field_value(obj, field_name) for field_name in changes
    }
    for field_name, value in changes.items():
        setattr(obj, field_name, value)
    obj.full_clean()
    after_values = {field_name: _field_value(obj, field_name) for field_name in changes}
    changed_values = {
        field_name: {
            "before": _json_safe(before_values[field_name]),
            "after": _json_safe(after_values[field_name]),
        }
        for field_name in changes
        if before_values[field_name] != after_values[field_name]
    }
    return ObjectChange(
        object_pk=_json_safe(obj.pk),
        object_label=str(obj),
        object_identifier=public_identifier(obj),
        changes=changed_values,
    )


def _validate_changes(model: type[models.Model], changes: dict[str, Any]) -> None:
    if not changes:
        raise BulkEditValidationError(
            "Provide at least one field change to preview or apply."
        )

    for field_name in changes:
        try:
            model_field = model._meta.get_field(field_name)
        except FieldDoesNotExist as exc:
            raise BulkEditValidationError(
                f"{field_name!r} is not a field on {model_label(model)}."
            ) from exc
        if model_field.primary_key or getattr(model_field, "auto_created", False):
            raise BulkEditValidationError(f"{field_name!r} cannot be bulk edited.")
        if (
            model_field.many_to_many
            or model_field.one_to_many
            or model_field.many_to_one
            and model_field.auto_created
        ):
            raise BulkEditValidationError(
                f"{field_name!r} relations cannot be bulk edited with this helper."
            )
        if not getattr(model_field, "editable", True):
            raise BulkEditValidationError(f"{field_name!r} is not editable.")


def _field_value(obj: models.Model, field_name: str) -> Any:
    model_field = obj._meta.get_field(field_name)
    if getattr(model_field, "attname", None):
        return getattr(obj, model_field.attname)
    return getattr(obj, field_name)


def _usable_lookup_fields(
    model: type[models.Model], lookup_fields: Iterable[str]
) -> list[str]:
    fields: list[str] = []
    for field_name in lookup_fields:
        if field_name == "pk":
            fields.append(field_name)
            continue
        try:
            field = model._meta.get_field(field_name)
        except FieldDoesNotExist:
            continue
        if not field.is_relation and not field.many_to_many:
            fields.append(field_name)
    return fields


def _candidate_filters(
    model: type[models.Model],
    raw_identifier: Any,
    lookup_fields: Iterable[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    raw_text = str(raw_identifier).strip()
    for field_name in lookup_fields:
        if field_name == "pk":
            pk_value = _coerce_pk(model, raw_identifier)
            if pk_value is not None:
                candidates.append({"pk": pk_value})
            continue

        model_field = model._meta.get_field(field_name)
        if isinstance(model_field, models.UUIDField):
            try:
                candidates.append({field_name: uuid.UUID(raw_text)})
            except (TypeError, ValueError, AttributeError):
                continue
        else:
            candidates.append({field_name: raw_identifier})
    return candidates


def _coerce_pk(model: type[models.Model], raw_identifier: Any) -> Any | None:
    pk_field = model._meta.pk
    try:
        return pk_field.to_python(raw_identifier)
    except (TypeError, ValueError, ValidationError):
        return None


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
