from __future__ import annotations

import os
import uuid
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.db.models import Count, Q
from mcp.server.mcpserver import Context, MCPServer

from audit.integration_auth import ALL_SCOPES, authenticate_bearer_header
from core.models import InventoryItem, Product, ProductRecipeItem

MAX_PAGE_SIZE = 200


def _decimal(value):
    if value is None:
        return None
    return str(value) if isinstance(value, Decimal) else value


def _authorization_header(ctx: Context) -> str:
    headers = ctx.headers or {}
    return headers.get('authorization') or headers.get('Authorization') or ''


def _authorize(ctx: Context, required_scope: str, enforce_auth: bool):
    if not enforce_auth:
        return None
    return authenticate_bearer_header(_authorization_header(ctx), required_scope)


def _page_size(limit: int) -> int:
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError(f'limit must be between 1 and {MAX_PAGE_SIZE}.')
    return limit


def _product_dict(product: Product) -> dict:
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
            {
                'id': product.prep_station_ref_id,
                'code': product.prep_station_ref.code,
                'name_ar': product.prep_station_ref.name_ar,
            }
            if product.prep_station_ref_id
            else None
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


def _inventory_dict(item: InventoryItem) -> dict:
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
            if item.preferred_vendor_id
            else None
        ),
        'is_active': item.is_active,
        'is_low_stock': item.is_low_stock,
        'recipe_line_count': getattr(item, 'recipe_line_count', None),
        'updated_at': item.updated_at.isoformat(),
    }


def _recipe_dict(line: ProductRecipeItem) -> dict:
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


def build_server(*, enforce_auth: bool = True) -> MCPServer:
    server = MCPServer(
        'Hub Management',
        instructions=(
            'Read operational master data from Hub Sweida. Treat returned values as live production data. '
            'This MCP bridge is read-only in v1; use the separately controlled REST preview/apply contract '
            'for catalog writes until MCP write actions are explicitly enabled.'
        ),
    )

    @server.tool(name='hub_capabilities')
    def hub_capabilities(ctx: Context) -> dict:
        """Return Hub integration scopes and the current safe write boundary."""
        _authorize(ctx, 'schema.read', enforce_auth)
        return {
            'api_version': 'v1',
            'mcp_version': 'v1-read',
            'available_scopes': sorted(ALL_SCOPES),
            'mcp_write_tools_enabled': False,
            'rest_write_contract': 'preview_then_single_use_apply',
            'finance_writes': False,
            'inventory_writes': False,
            'recipe_writes': False,
            'destructive_operations': False,
        }

    @server.tool(name='hub_search_products')
    def hub_search_products(
        ctx: Context,
        q: str = '',
        limit: int = 50,
        available: bool | None = None,
        product_type: str = '',
    ) -> dict:
        """Search live Hub products by Arabic/English name or menu key."""
        _authorize(ctx, 'catalog.read', enforce_auth)
        limit = _page_size(limit)
        queryset = (
            Product.objects.select_related('category', 'prep_station_ref')
            .prefetch_related('menu_sections', 'tags')
            .annotate(recipe_line_count=Count('recipe_items', distinct=True))
            .order_by('sort_order', 'name_ar', 'pk')
        )
        q = (q or '').strip()
        if q:
            queryset = queryset.filter(
                Q(name_ar__icontains=q)
                | Q(name_en__icontains=q)
                | Q(metadata__masharib_menu_code__icontains=q)
            )
        if available is not None:
            queryset = queryset.filter(is_available=available)
        product_type = (product_type or '').strip()
        if product_type:
            queryset = queryset.filter(product_type=product_type)
        total = queryset.count()
        return {
            'count': total,
            'limit': limit,
            'items': [_product_dict(item) for item in queryset[:limit]],
        }

    @server.tool(name='hub_search_inventory')
    def hub_search_inventory(
        ctx: Context,
        q: str = '',
        limit: int = 50,
        active: bool | None = None,
        item_type: str = '',
        used_in_recipes_only: bool = False,
    ) -> dict:
        """Search live Hub inventory items, including current quantity, cost and recipe usage."""
        _authorize(ctx, 'inventory.read', enforce_auth)
        limit = _page_size(limit)
        queryset = (
            InventoryItem.objects.select_related('preferred_vendor')
            .annotate(recipe_line_count=Count('recipe_items', distinct=True))
            .order_by('name_ar', 'pk')
        )
        q = (q or '').strip()
        if q:
            queryset = queryset.filter(
                Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(code__icontains=q)
            )
        if active is not None:
            queryset = queryset.filter(is_active=active)
        item_type = (item_type or '').strip()
        if item_type:
            queryset = queryset.filter(item_type=item_type)
        if used_in_recipes_only:
            queryset = queryset.filter(recipe_line_count__gt=0)
        total = queryset.count()
        return {
            'count': total,
            'limit': limit,
            'items': [_inventory_dict(item) for item in queryset[:limit]],
        }

    @server.tool(name='hub_get_recipe_lines')
    def hub_get_recipe_lines(
        ctx: Context,
        product: str,
        limit: int = 100,
        active: bool | None = None,
    ) -> dict:
        """Return recipe lines for a product UUID, menu key, or exact Arabic name."""
        _authorize(ctx, 'recipes.read', enforce_auth)
        limit = _page_size(limit)
        product = (product or '').strip()
        if not product:
            raise ValueError('product is required.')

        product_filter = Q(product__metadata__masharib_menu_code=product) | Q(product__name_ar__iexact=product)
        try:
            product_uuid = uuid.UUID(product)
        except (ValueError, AttributeError):
            product_uuid = None
        if product_uuid is not None:
            product_filter |= Q(product__public_code=product_uuid)

        queryset = (
            ProductRecipeItem.objects.select_related('product', 'inventory_item')
            .filter(product_filter)
            .order_by('product__name_ar', 'pk')
        )
        if active is not None:
            queryset = queryset.filter(is_active=active)
        total = queryset.count()
        return {
            'count': total,
            'limit': limit,
            'items': [_recipe_dict(item) for item in queryset[:limit]],
        }

    return server


mcp = build_server(enforce_auth=True)
