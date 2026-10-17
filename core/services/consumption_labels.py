"""Clear customer-facing labels for zero-balance, categorized POS consumption.

A zero payable is not proof of a cash payment. Classifications are based only
on attached, active consumption discounts, and never rewrite payment records.
"""
from core.models import OrderConsumption


def zero_balance_consumption_label(orders, fallback='ضيافة'):
    orders = [order for order in orders if order.pk]
    if not orders:
        return fallback
    gross = sum(order.subtotal_syp + order.service_fee_syp
                + max(order.delivery_fee_syp or 0, 0) for order in orders)
    if not gross or any(order.total_syp != 0 or order.paid_syp for order in orders):
        return fallback
    rows = list(OrderConsumption.objects.filter(
        order_id__in=[order.pk for order in orders],
        discount__is_active=True,
    ).values_list('category', 'retail_value_syp'))
    if not rows or sum(amount for _, amount in rows) != gross:
        # A different discount or a partially priced order also reduced the bill.
        return fallback
    categories = {category for category, _amount in rows}
    if categories == {OrderConsumption.Category.OWNER_PERSONAL}:
        return 'استخدام شخصي للمالك'
    if categories == {OrderConsumption.Category.HUB_HOSPITALITY}:
        return 'ضيافة هَب'
    if categories == {OrderConsumption.Category.OWNER_PERSONAL, OrderConsumption.Category.HUB_HOSPITALITY}:
        return 'ضيافة هَب واستخدام شخصي'
    return fallback
