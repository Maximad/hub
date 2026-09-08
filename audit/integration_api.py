from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone

from core.models import ActivityLog, InventoryItem, Product, ProductRecipeItem
from core.services.bulk_edit import BulkEditValidationError
from core.services.menu_tools import (
    ALLOWED_ACTIONS,
    apply_product_bulk_action,
    preview_product_bulk_action,
)

from .integration_auth import (
    ALL_SCOPES,
    confirmation_ttl_seconds,
    integration_endpoint,
)
from .models import IntegrationMutationApproval

CONFIRMATION_SALT = 'hub.management.catalog.preview.v1'
MAX_PAGE_SIZE = 200


def _response(request, data, *, status=200):
    body = {'ok': status < 400, **data, 'request_id': str(request.integration_request_id)}
    return JsonResponse(body, status=status, json_dumps_params={'ensure_ascii': False})


def _error(request, code, message, *, status=400, details=None):
    error = {'code': code, 'message': message}
    if details is not None:
        error['details'] = details
    return _response(request, {'error': error}, status=status)


def _json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Request body must be valid UTF-8 JSON.') from exc
    if not isinstance(data, dict):
        raise ValueError('Request body must be a JSON object.')
    return data


def _decimal(value):
    if value is None:
        return None
    return str(value) if isinstance(value, Decimal) else value


def _pagination(request):
    try:
        limit = int(request.GET.get('limit', 100))
        offset = int(request.GET.get('offset', 0))
    except ValueError as exc:
        raise ValueError('limit and offset must be integers.') from exc
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError(f'limit must be between 1 and {MAX_PAGE_SIZE}.')
    if offset < 0:
        raise ValueError('offset must be zero or greater.')
    return limit, offset


def _canonical_payload(payload):
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')


def _payload_digest(payload):
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def _product_dict(product):
    return {
        'id': product.pk,
        'public_code': str(product.public_code),
        'key': (product.metadata or {}).get('masharib_menu_code'),
        'name_ar': product.name_ar,
        'name_en': product.name_en,
        'description_ar': product.description_ar,
        'description_en': product.description_en,
        'category': {
            'id': product.category_id,
            'name_ar': product.category.name_ar,
            'name_en': product.category.name_en,
        },
        'price_syp': product.price_syp,
        'estimated_unit_cost_syp': product.estimated_unit_cost_syp,
        'cost_syp': product.cost_syp,
        'is_available': product.is_available,
        'visible_on_pos': product.visible_on_pos,
        'orderable_on_pos': product.orderable_on_pos,
        'visible_on_qr': product.visible_on_qr,
        'orderable_on_qr': product.orderable_on_qr,
        'product_type': product.product_type,
        'item_type': product.item_type,
        'beverage_type': product.beverage_type,
        'food_type': product.food_type,
        'service_type': product.service_type,
        'requires_preparation': product.requires_preparation,
        'prep_station': (
            {'id': product.prep_station_ref_id, 'code': product.prep_station_ref.code, 'name_ar': product.prep_station_ref.name_ar}
            if product.prep_station_ref_id else None
        ),
        'menu_sections': [
            {'id': section.pk, 'name_ar': section.name_ar, 'name_en': section.name_en}
            for section in product.menu_sections.all()
        ],
        'tags': [
            {'id': tag.pk, 'name_ar': tag.name_ar, 'name_en': tag.name_en}
            for tag in product.tags.all()
        ],
        'recipe_line_count': getattr(product, 'recipe_line_count', None),
        'updated_at': product.updated_at.isoformat(),
    }


def _inventory_dict(item):
    return {
        'id': item.pk,
        'code': item.code,
        'name_ar': item.name_ar,
        'name_en': item.name_en,
        'item_type': item.item_type,
        'unit': item.unit,
        'current_quantity': _decimal(item.current_quantity),
        'low_stock_threshold': _decimal(item.low_stock_threshold),
        'estimated_unit_cost_syp': _decimal(item.estimated_unit_cost_syp),
        'preferred_vendor': (
            {'id': item.preferred_vendor_id, 'name': str(item.preferred_vendor)}
            if item.preferred_vendor_id else None
        ),
        'is_active': item.is_active,
        'is_low_stock': item.is_low_stock,
        'recipe_line_count': getattr(item, 'recipe_line_count', None),
        'updated_at': item.updated_at.isoformat(),
    }


def _recipe_dict(line):
    return {
        'id': line.pk,
        'product': {
            'id': line.product_id,
            'public_code': str(line.product.public_code),
            'key': (line.product.metadata or {}).get('masharib_menu_code'),
            'name_ar': line.product.name_ar,
        },
        'inventory_item': {
            'id': line.inventory_item_id,
            'code': line.inventory_item.code,
            'name_ar': line.inventory_item.name_ar,
            'unit': line.inventory_item.unit,
            'is_active': line.inventory_item.is_active,
            'estimated_unit_cost_syp': _decimal(line.inventory_item.estimated_unit_cost_syp),
        },
        'quantity_per_unit': _decimal(line.quantity_per_unit),
        'unit': line.unit,
        'waste_factor_percent': _decimal(line.waste_factor_percent),
        'line_cost_syp': _decimal(line.line_cost()),
        'is_active': line.is_active,
        'notes': line.notes,
        'updated_at': line.updated_at.isoformat(),
    }


def _change_dict(change):
    return {
        'object_pk': change.object_pk,
        'object_label': change.object_label,
        'object_identifier': change.object_identifier,
        'changes': change.changes,
    }


def _annotate_activity_logs(result, request):
    for log in ActivityLog.objects.filter(pk__in=(result.activity_log_ids or [])):
        details = dict(log.details or {})
        details['integration'] = {
            'token_id': request.integration_token.pk,
            'token_prefix': request.integration_token.prefix,
            'token_name': request.integration_token.name,
            'request_id': str(request.integration_request_id),
        }
        log.details = details
        log.save(update_fields=['details'])


@integration_endpoint('schema.read', methods=('GET',))
def management_schema(request):
    return _response(request, {
        'api_version': 'v1',
        'scopes': sorted(ALL_SCOPES),
        'safe_catalog_actions': [
            {'code': code, 'label': label}
            for code, label in sorted(ALLOWED_ACTIONS.items())
        ],
        'write_contract': {
            'catalog': 'preview_then_single_use_apply',
            'confirmation_ttl_seconds': confirmation_ttl_seconds(),
            'destructive_operations': False,
            'finance_writes': False,
            'inventory_writes': False,
            'recipe_writes': False,
        },
    })


@integration_endpoint('catalog.read', methods=('GET',))
def products_list(request):
    try:
        limit, offset = _pagination(request)
    except ValueError as exc:
        return _error(request, 'invalid_pagination', str(exc))

    queryset = (
        Product.objects.select_related('category', 'prep_station_ref')
        .prefetch_related('menu_sections', 'tags')
        .annotate(recipe_line_count=Count('recipe_items', distinct=True))
        .order_by('sort_order', 'name_ar', 'pk')
    )
    q = (request.GET.get('q') or '').strip()
    if q:
        queryset = queryset.filter(
            Q(name_ar__icontains=q)
            | Q(name_en__icontains=q)
            | Q(metadata__masharib_menu_code__icontains=q)
        )
    if request.GET.get('available') in {'0', '1'}:
        queryset = queryset.filter(is_available=request.GET['available'] == '1')
    product_type = (request.GET.get('product_type') or '').strip()
    if product_type:
        queryset = queryset.filter(product_type=product_type)

    total = queryset.count()
    items = [_product_dict(item) for item in queryset[offset:offset + limit]]
    return _response(request, {'count': total, 'limit': limit, 'offset': offset, 'items': items})


@integration_endpoint('inventory.read', methods=('GET',))
def inventory_list(request):
    try:
        limit, offset = _pagination(request)
    except ValueError as exc:
        return _error(request, 'invalid_pagination', str(exc))

    queryset = (
        InventoryItem.objects.select_related('preferred_vendor')
        .annotate(recipe_line_count=Count('recipe_items', distinct=True))
        .order_by('name_ar', 'pk')
    )
    q = (request.GET.get('q') or '').strip()
    if q:
        queryset = queryset.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(code__icontains=q))
    if request.GET.get('active') in {'0', '1'}:
        queryset = queryset.filter(is_active=request.GET['active'] == '1')
    item_type = (request.GET.get('item_type') or '').strip()
    if item_type:
        queryset = queryset.filter(item_type=item_type)

    total = queryset.count()
    items = [_inventory_dict(item) for item in queryset[offset:offset + limit]]
    return _response(request, {'count': total, 'limit': limit, 'offset': offset, 'items': items})


@integration_endpoint('recipes.read', methods=('GET',))
def recipes_list(request):
    try:
        limit, offset = _pagination(request)
    except ValueError as exc:
        return _error(request, 'invalid_pagination', str(exc))

    queryset = ProductRecipeItem.objects.select_related('product', 'inventory_item').order_by('product__name_ar', 'pk')
    product = (request.GET.get('product') or '').strip()
    if product:
        product_filter = (
            Q(product__metadata__masharib_menu_code=product)
            | Q(product__name_ar__iexact=product)
        )
        try:
            product_uuid = uuid.UUID(product)
        except (ValueError, AttributeError):
            product_uuid = None
        if product_uuid is not None:
            product_filter |= Q(product__public_code=product_uuid)
        queryset = queryset.filter(product_filter)
    if request.GET.get('active') in {'0', '1'}:
        queryset = queryset.filter(is_active=request.GET['active'] == '1')

    total = queryset.count()
    items = [_recipe_dict(item) for item in queryset[offset:offset + limit]]
    return _response(request, {'count': total, 'limit': limit, 'offset': offset, 'items': items})


@integration_endpoint('catalog.write', methods=('POST',))
def catalog_preview(request):
    try:
        data = _json_body(request)
        identifiers = data.get('identifiers') or []
        if not isinstance(identifiers, list):
            raise ValueError('identifiers must be a JSON list.')
        if len(identifiers) > 100:
            raise ValueError('A single catalog change may target at most 100 products.')
        action = str(data.get('action') or '').strip()
        value = str(data.get('value') if data.get('value') is not None else '').strip()
        result = preview_product_bulk_action(identifiers, action, value)
    except (ValueError, BulkEditValidationError, ValidationError) as exc:
        return _error(request, 'invalid_catalog_change', str(exc))

    mutation_payload = {
        'identifiers': identifiers,
        'action': action,
        'value': value,
    }
    expires_at = timezone.now() + timedelta(seconds=confirmation_ttl_seconds())
    approval = IntegrationMutationApproval.objects.create(
        token=request.integration_token,
        operation='catalog.write',
        payload_digest=_payload_digest(mutation_payload),
        expires_at=expires_at,
    )
    signed_payload = {
        'token_pk': request.integration_token.pk,
        'approval_nonce': str(approval.nonce),
        'mutation': mutation_payload,
    }
    confirmation_token = signing.dumps(
        signed_payload,
        salt=CONFIRMATION_SALT,
        compress=True,
    )
    return _response(request, {
        'preview': {
            'action': {'code': result.action.code, 'label': result.action.label, 'target': result.action.target_label},
            'requested_count': result.requested_count,
            'matched_count': result.matched_count,
            'changes': [_change_dict(change) for change in result.changes],
        },
        'confirmation_token': confirmation_token,
        'confirmation_expires_in_seconds': confirmation_ttl_seconds(),
        'confirmation_expires_at': expires_at.isoformat(),
    })


@integration_endpoint('catalog.write', methods=('POST',))
def catalog_apply(request):
    try:
        data = _json_body(request)
        confirmation_token = str(data.get('confirmation_token') or '').strip()
        if not confirmation_token:
            raise ValueError('confirmation_token is required.')
        signed_payload = signing.loads(
            confirmation_token,
            salt=CONFIRMATION_SALT,
            max_age=confirmation_ttl_seconds(),
        )
        if signed_payload.get('token_pk') != request.integration_token.pk:
            return _error(
                request,
                'confirmation_token_mismatch',
                'Confirmation token belongs to a different integration credential.',
                status=403,
            )

        mutation_payload = signed_payload.get('mutation')
        if not isinstance(mutation_payload, dict):
            return _error(request, 'invalid_confirmation', 'Confirmation payload is invalid.')
        approval_nonce = signed_payload.get('approval_nonce')
        try:
            approval_uuid = uuid.UUID(str(approval_nonce))
        except (ValueError, TypeError, AttributeError):
            return _error(request, 'invalid_confirmation', 'Confirmation approval identifier is invalid.')

        with transaction.atomic():
            try:
                approval = IntegrationMutationApproval.objects.select_for_update().get(
                    nonce=approval_uuid,
                    token=request.integration_token,
                    operation='catalog.write',
                )
            except IntegrationMutationApproval.DoesNotExist:
                return _error(request, 'invalid_confirmation', 'Confirmation approval does not exist.', status=403)

            if approval.consumed_at is not None:
                return _error(
                    request,
                    'confirmation_already_used',
                    'Confirmation token has already been used.',
                    status=409,
                )
            if approval.expires_at <= timezone.now():
                return _error(request, 'confirmation_expired', 'Confirmation token has expired.')
            if not hmac.compare_digest(
                approval.payload_digest,
                _payload_digest(mutation_payload),
            ):
                return _error(request, 'invalid_confirmation', 'Confirmation payload does not match its approved preview.', status=403)

            result = apply_product_bulk_action(
                mutation_payload.get('identifiers') or [],
                mutation_payload.get('action') or '',
                mutation_payload.get('value') or '',
                actor=None,
            )
            _annotate_activity_logs(result, request)
            approval.consumed_at = timezone.now()
            approval.save(update_fields=['consumed_at'])
    except signing.SignatureExpired:
        return _error(request, 'confirmation_expired', 'Confirmation token has expired.')
    except signing.BadSignature:
        return _error(request, 'invalid_confirmation', 'Confirmation token is invalid.')
    except (ValueError, BulkEditValidationError, ValidationError) as exc:
        return _error(request, 'invalid_catalog_change', str(exc))

    return _response(request, {
        'applied': {
            'action': {'code': result.action.code, 'label': result.action.label, 'target': result.action.target_label},
            'requested_count': result.requested_count,
            'matched_count': result.matched_count,
            'changes': [_change_dict(change) for change in result.changes],
            'activity_log_ids': result.activity_log_ids or [],
        }
    })
