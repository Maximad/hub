"""Day-close, daily reporting, and management performance views."""
from datetime import timedelta
from types import SimpleNamespace

from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import require_staff_capability
from core.finance import finance_summary_for_date, purchase_totals_for_date
from core.models import Order, OrderItem
from core.views_legacy import (
    DAMASCUS_TZ,
    _build_day_report,
    _day_range_utc,
    _parse_range_dates,
    _product_margin_context,
    staff_reports_home,
    staff_reports_day,
    staff_reports_day_csv,
    staff_product_margin_report,
    staff_product_margin_csv,
    staff_close_day_print,
)
from operations.views import staff_business_day as staff_close_day


HISTORICAL_ORDER_PREFIX = '[PRE_SYSTEM_HISTORICAL_SALES]'


def _performance_dates(request):
    """Default to the current month plus the preceding three calendar months."""
    if request.GET.get('date_from') or request.GET.get('date_to'):
        return _parse_range_dates(request)
    today = timezone.now().astimezone(DAMASCUS_TZ).date()
    first_of_month = today.replace(day=1)
    date_from = (first_of_month - timedelta(days=92)).replace(day=1)
    return date_from, today


def _pct_change(current, previous):
    if previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 1)


def _new_metrics():
    return {
        'units_sold': 0,
        'orders_count': 0,
        'product_sales_syp': 0,
        'gross_sales_syp': 0,
        'discounts_syp': 0,
        'net_sales_syp': 0,
        'estimated_cost_syp': 0,
        'estimated_margin_syp': 0,
        'margin_known_sales_syp': 0,
        'expenses_syp': 0,
        'purchases_syp': 0,
        'purchases_paid_syp': 0,
        'payments_on_orders_syp': 0,
        'cash_on_orders_syp': 0,
        'transfer_on_orders_syp': 0,
        'comp_value_syp': 0,
        'internet_workspace_revenue_syp': 0,
    }


def _decorate_metrics(metrics, *, historical=False):
    product_sales = metrics['product_sales_syp']
    known_sales = metrics['margin_known_sales_syp']
    margin = metrics['estimated_margin_syp']
    metrics['cost_coverage_percent'] = (
        round((known_sales / product_sales) * 100, 1) if product_sales else None
    )
    metrics['estimated_margin_percent'] = (
        round((margin / known_sales) * 100, 1) if known_sales else None
    )
    complete_cost = product_sales > 0 and known_sales >= product_sales
    # Purchases are intentionally not subtracted here: they create inventory and
    # would double-count cost of goods already represented by estimated COGS.
    metrics['recorded_operating_result_syp'] = (
        metrics['net_sales_syp'] - metrics['estimated_cost_syp'] - metrics['expenses_syp']
        if complete_cost and not historical
        else None
    )
    metrics['historical_import'] = historical
    return metrics


@require_staff_capability('reports')
def staff_performance_report(request):
    """Range-based management view built from Hub's existing report calculations."""
    date_from, date_to = _performance_dates(request)
    start_utc, _ = _day_range_utc(date_from)
    _, end_utc = _day_range_utc(date_to)

    historical_orders = Order.objects.filter(
        created_at__gte=start_utc,
        created_at__lt=end_utc,
        notes__startswith=HISTORICAL_ORDER_PREFIX,
    )
    historical_months = {
        timezone.localtime(order.created_at, DAMASCUS_TZ).strftime('%Y-%m')
        for order in historical_orders.only('created_at')
    }
    historical_needs_review = OrderItem.objects.filter(
        order__in=historical_orders,
        item_note__contains='review=needs_review',
    ).count()

    totals = _new_metrics()
    monthly = {}
    current = date_from
    while current <= date_to:
        rows, sums = _build_day_report(current)
        finance = finance_summary_for_date(current, sums)
        purchases = purchase_totals_for_date(current)
        units = sum(
            sum(item.quantity for item in row['order'].items.all())
            for row in rows
            if row['order'].status != Order.Status.CANCELLED
        )
        day_values = {
            'units_sold': units,
            'orders_count': int(sums.get('orders_count') or 0),
            'product_sales_syp': int(sums.get('subtotal_total') or 0),
            'gross_sales_syp': int(sums.get('gross_sales') or 0),
            'discounts_syp': int(sums.get('discounts_syp') or 0),
            'net_sales_syp': int(sums.get('net_sales') or 0),
            'estimated_cost_syp': int(sums.get('estimated_cost_syp') or 0),
            'estimated_margin_syp': int(sums.get('estimated_gross_margin_syp') or 0),
            'margin_known_sales_syp': int(sums.get('margin_known_sales_syp') or 0),
            'expenses_syp': int(finance.get('expenses_total_syp') or 0),
            'purchases_syp': int(purchases.get('total') or 0),
            'purchases_paid_syp': int(purchases.get('paid') or 0),
            'payments_on_orders_syp': int(sums.get('paid_total') or 0),
            'cash_on_orders_syp': int(sums.get('cash_total') or 0),
            'transfer_on_orders_syp': int(sums.get('manual_transfer_total') or 0),
            'comp_value_syp': int(sums.get('comp_value_syp') or 0),
            'internet_workspace_revenue_syp': int(sums.get('internet_workspace_revenue_syp') or 0),
        }
        month_key = current.strftime('%Y-%m')
        bucket = monthly.setdefault(month_key, _new_metrics())
        for key, value in day_values.items():
            totals[key] += value
            bucket[key] += value
        current += timedelta(days=1)

    monthly_rows = []
    previous = None
    for month_key in sorted(monthly):
        row = monthly[month_key]
        _decorate_metrics(row, historical=month_key in historical_months)
        row['month'] = month_key
        row['revenue_change_percent'] = _pct_change(
            row['gross_sales_syp'], previous['gross_sales_syp'] if previous else None
        )
        row['units_change_percent'] = _pct_change(
            row['units_sold'], previous['units_sold'] if previous else None
        )
        monthly_rows.append(row)
        previous = row

    has_historical = bool(historical_months)
    _decorate_metrics(totals, historical=has_historical)

    # Reuse the existing product-margin engine, but do not hide inactive legacy
    # products: historical sales can legitimately reference them.
    product_params = request.GET.copy()
    product_params['date_from'] = date_from.isoformat()
    product_params['date_to'] = date_to.isoformat()
    product_params['only_active'] = '0'
    product_params['only_sold'] = '1'
    product_context = _product_margin_context(SimpleNamespace(GET=product_params))
    sold_rows = [row for row in product_context['rows'] if row['units_sold'] > 0]
    missing_cost_rows = [row for row in sold_rows if row['missing_cost']]

    quality_notes = []
    if has_historical:
        quality_notes.append(
            'تتضمن الفترة مبيعات تاريخية مجمعة قبل تشغيل النظام. عدد الطلبات ومتوسط الطلب والتحصيل لا يمثلان الواقع التاريخي.'
        )
        if totals['payments_on_orders_syp'] == 0:
            quality_notes.append(
                'لم تُنشأ دفعات وهمية للمبيعات التاريخية؛ الصفر في التحصيل التاريخي لا يعني وجود ذمم على الزبائن.'
            )
        if totals['expenses_syp'] == 0:
            quality_notes.append('لا توجد مصروفات تاريخية مسجلة ضمن هذه الفترة حتى الآن.')
        if totals['purchases_syp'] == 0:
            quality_notes.append('لا توجد مشتريات تاريخية مسجلة ضمن هذه الفترة حتى الآن.')
    if historical_needs_review:
        quality_notes.append(
            f'هناك {historical_needs_review} صفاً تاريخياً معلماً للمراجعة التفصيلية دون أن يمنع احتسابه في المبيعات.'
        )
    if missing_cost_rows:
        quality_notes.append(
            f'هناك {len(missing_cost_rows)} منتجاً مبيعاً بلا كلفة مقدرة؛ الهامش والنتيجة التشغيلية غير مكتملين.'
        )

    today = timezone.now().astimezone(DAMASCUS_TZ).date()
    current_month_start = today.replace(day=1)
    presets = [
        {'label': 'تموز 2026', 'date_from': '2026-07-01', 'date_to': '2026-07-31'},
        {'label': 'آب 2026', 'date_from': '2026-08-01', 'date_to': '2026-08-31'},
        {'label': 'أيلول 2026', 'date_from': '2026-09-01', 'date_to': '2026-09-30'},
        {'label': 'تموز–أيلول 2026', 'date_from': '2026-07-01', 'date_to': '2026-09-30'},
        {'label': 'الشهر الحالي', 'date_from': current_month_start.isoformat(), 'date_to': today.isoformat()},
    ]

    context = {
        'date_from': date_from,
        'date_to': date_to,
        'totals': totals,
        'monthly_rows': monthly_rows,
        'top_products': product_context['cards']['best_revenue'][:5],
        'top_categories': product_context['section_rows'][:5],
        'missing_cost_rows': missing_cost_rows,
        'historical_months': sorted(historical_months),
        'historical_needs_review': historical_needs_review,
        'quality_notes': quality_notes,
        'presets': presets,
    }
    return render(request, 'staff/reports_performance.html', context)
