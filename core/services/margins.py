UNKNOWN_LABEL = 'غير محدد'


def money(value):
    return int(value or 0)


def product_estimated_unit_cost(product):
    return getattr(product, 'estimated_unit_cost_syp', None)


def item_revenue_syp(item):
    line_total = getattr(item, 'line_total_syp_snapshot', None)
    if line_total is not None:
        return money(line_total)
    return money(getattr(item, 'quantity', 0)) * money(getattr(item, 'unit_price_syp_snapshot', 0))


def item_estimated_cost_syp(item, allow_current_product_cost=True):
    cost = getattr(item, 'estimated_line_cost_syp_snapshot', None)
    if cost is not None:
        return money(cost), False
    unit_cost = getattr(item, 'estimated_unit_cost_syp_snapshot', None)
    if unit_cost is not None:
        return money(unit_cost) * money(getattr(item, 'quantity', 0)), False
    if allow_current_product_cost and getattr(item, 'product_id', None):
        current_cost = product_estimated_unit_cost(item.product)
        if current_cost is not None:
            return money(current_cost) * money(getattr(item, 'quantity', 0)), True
    return None, False


def margin_percent(revenue, margin):
    revenue = money(revenue)
    if revenue <= 0 or margin is None:
        return None
    return round((margin / revenue) * 100, 2)


def item_margin(item, allow_current_product_cost=True):
    revenue = item_revenue_syp(item)
    cost, used_current_cost = item_estimated_cost_syp(item, allow_current_product_cost=allow_current_product_cost)
    margin = None if cost is None else revenue - cost
    return {
        'revenue_syp': revenue,
        'estimated_cost_syp': cost,
        'estimated_margin_syp': margin,
        'estimated_margin_percent': margin_percent(revenue, margin),
        'missing_cost': cost is None,
        'used_current_cost': used_current_cost,
    }


def product_margin_from_values(revenue_syp, estimated_cost_syp):
    revenue = money(revenue_syp)
    if estimated_cost_syp is None:
        return None, None
    margin = revenue - money(estimated_cost_syp)
    return margin, margin_percent(revenue, margin)


def product_unit_margin(product):
    cost = product_estimated_unit_cost(product)
    if cost is None:
        return None, None
    return product_margin_from_values(getattr(product, 'price_syp', 0), cost)
