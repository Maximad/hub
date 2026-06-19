from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ActivityLog, OrderItem, Product, ProductRecipeItem, ProductionBatch, StockMovement, SystemSetting
from .settings_helpers import get_system_settings


def calculate_recipe_cost(product):
    items = product.recipe_items.select_related('inventory_item').filter(is_active=True)
    total = Decimal('0')
    missing = []
    for item in items:
        cost = item.line_cost()
        if cost is None:
            missing.append(item.inventory_item.name_ar)
            continue
        total += cost
    price = Decimal(product.price_syp or 0)
    margin = price - total
    percent = (margin / price * Decimal('100')) if price else None
    return {
        'recipe_estimated_unit_cost_syp': total,
        'recipe_margin_syp': margin,
        'recipe_margin_percent': percent,
        'missing_cost_items': missing,
    }


def update_product_cost_from_recipe(product, user=None):
    result = calculate_recipe_cost(product)
    product.estimated_unit_cost_syp = int(result['recipe_estimated_unit_cost_syp'])
    product.cost_updated_at = timezone.now()
    product.cost_updated_by = user if getattr(user, 'is_authenticated', False) else None
    product.cost_notes = ((product.cost_notes or '') + '\nتم تحديث الكلفة من الوصفة.').strip()
    product.save(update_fields=['estimated_unit_cost_syp', 'cost_updated_at', 'cost_updated_by', 'cost_notes', 'updated_at'])
    try:
        ActivityLog.objects.create(actor=user if getattr(user, 'is_authenticated', False) else None, action='تحديث الكلفة من الوصفة', details={'model': 'Product', 'object_id': str(product.pk), 'recipe_cost': str(result['recipe_estimated_unit_cost_syp'])})
    except Exception:
        pass
    return result


def deduction_settings():
    settings = get_system_settings()
    enabled = bool(settings.auto_deduct_inventory_on_sale and settings.stock_deduction_mode != SystemSetting.StockDeductionMode.DISABLED)
    return settings, enabled


def deduct_order_item_stock(order_item, user=None, strict=None, trigger=SystemSetting.StockDeductionMode.ON_ORDER_CREATED):
    settings, enabled = deduction_settings()
    if not enabled or settings.stock_deduction_mode != trigger:
        return False, ''
    if order_item.stock_deducted:
        return False, 'تم خصم المخزون مسبقاً'
    strict = settings.strict_stock_deduction if strict is None else strict
    recipes = list(order_item.product.recipe_items.select_related('inventory_item').filter(is_active=True))
    if not recipes:
        msg = 'لا توجد وصفة للمنتج'
        OrderItem.objects.filter(pk=order_item.pk, stock_deducted=False).update(stock_deduction_error=msg)
        return False, msg
    shortages = []
    for recipe in recipes:
        needed = recipe.quantity_per_unit * Decimal(order_item.quantity)
        if recipe.inventory_item.current_quantity < needed:
            shortages.append(f'{recipe.inventory_item.name_ar}: {needed} > {recipe.inventory_item.current_quantity}')
    if shortages:
        msg = 'المخزون غير كافٍ: ' + '، '.join(shortages)
        OrderItem.objects.filter(pk=order_item.pk).update(stock_deduction_error=msg)
        if strict:
            raise ValidationError(msg)
        return False, msg
    with transaction.atomic():
        locked = OrderItem.objects.select_for_update().get(pk=order_item.pk)
        if locked.stock_deducted:
            return False, 'تم خصم المخزون مسبقاً'
        for recipe in recipes:
            inv = recipe.inventory_item
            qty = recipe.quantity_per_unit * Decimal(locked.quantity)
            mv = StockMovement(inventory_item=inv, business_date=timezone.localdate(), movement_type=StockMovement.MovementType.SALE_DEDUCTION, direction=StockMovement.Direction.OUT, quantity=qty, unit=recipe.unit, unit_cost_syp=inv.estimated_unit_cost_syp, total_value_syp=(inv.estimated_unit_cost_syp or 0) * qty, related_order=locked.order, related_order_item=locked, product=locked.product, reason='خصم المخزون عند البيع', created_by=user if getattr(user, 'is_authenticated', False) else None)
            mv.full_clean(); mv.save(); mv.apply_to_stock()
        locked.stock_deducted = True; locked.stock_deducted_at = timezone.now(); locked.stock_deduction_error = ''
        locked.save(update_fields=['stock_deducted', 'stock_deducted_at', 'stock_deduction_error', 'updated_at'])
    return True, ''


def complete_production_batch(batch, user=None):
    if batch.status == ProductionBatch.Status.COMPLETED:
        raise ValidationError('لا يمكن إكمال دفعة مكتملة مسبقاً.')
    with transaction.atomic():
        batch = ProductionBatch.objects.select_for_update().get(pk=batch.pk)
        total = Decimal('0')
        for ing in batch.ingredients.select_related('inventory_item'):
            if ing.actual_quantity and ing.inventory_item.current_quantity < ing.actual_quantity:
                raise ValidationError(f'المخزون غير كافٍ: {ing.inventory_item.name_ar}')
            if ing.actual_quantity:
                mv = StockMovement(inventory_item=ing.inventory_item, business_date=batch.business_date, movement_type=StockMovement.MovementType.PRODUCTION_CONSUMPTION, direction=StockMovement.Direction.OUT, quantity=ing.actual_quantity, unit=ing.unit, unit_cost_syp=ing.estimated_unit_cost_syp_snapshot, total_value_syp=ing.estimated_line_cost_syp_snapshot, related_batch=batch, reason='استهلاك مكوّنات دفعة تحضير', created_by=user if getattr(user, 'is_authenticated', False) else None)
                mv.full_clean(); mv.save(); mv.apply_to_stock(); total += mv.total_value_syp or 0
        if batch.output_inventory_item_id and batch.output_quantity > 0:
            mv = StockMovement(inventory_item=batch.output_inventory_item, business_date=batch.business_date, movement_type=StockMovement.MovementType.PRODUCTION_OUTPUT, direction=StockMovement.Direction.IN, quantity=batch.output_quantity, unit=batch.output_unit or batch.output_inventory_item.unit, unit_cost_syp=batch.estimated_unit_cost_syp, total_value_syp=batch.total_estimated_cost_syp or total, related_batch=batch, reason='ناتج دفعة تحضير', created_by=user if getattr(user, 'is_authenticated', False) else None)
            mv.full_clean(); mv.save(); mv.apply_to_stock()
        batch.status = ProductionBatch.Status.COMPLETED; batch.completed_at = timezone.now(); batch.prepared_by = batch.prepared_by or (user if getattr(user, 'is_authenticated', False) else None); batch.total_estimated_cost_syp = batch.total_estimated_cost_syp or total
        if batch.produced_quantity and not batch.estimated_unit_cost_syp: batch.estimated_unit_cost_syp = total / batch.produced_quantity
        batch.save()
    return batch
