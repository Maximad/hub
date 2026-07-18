from django.db.models import Sum
from django.utils import timezone
from core.models import CashMovement, Expense, Payment


def finance_summary_for_date(day, base_sums=None):
    base_sums = base_sums or {}
    expenses = Expense.objects.filter(business_date=day)
    movements = CashMovement.objects.filter(business_date=day, is_cancelled=False)
    paid_cash_expenses = expenses.filter(status=Expense.Status.PAID, paid_from=Expense.PaidFrom.CASHBOX, payment_method=Expense.PaymentMethod.CASH)
    opening_cash = movements.filter(movement_type=CashMovement.MovementType.OPENING_CASH, direction=CashMovement.Direction.IN).aggregate(v=Sum('amount_syp'))['v'] or 0
    cash_in = movements.filter(direction=CashMovement.Direction.IN).exclude(movement_type=CashMovement.MovementType.OPENING_CASH).aggregate(v=Sum('amount_syp'))['v'] or 0
    cash_out_movements = movements.filter(direction=CashMovement.Direction.OUT).exclude(related_expense__isnull=False).aggregate(v=Sum('amount_syp'))['v'] or 0
    cash_expenses = paid_cash_expenses.aggregate(v=Sum('amount_syp'))['v'] or 0
    expected = opening_cash + (base_sums.get('cash_total') or 0) + cash_in - cash_out_movements - cash_expenses
    latest_close = None
    try:
        from core.models import DailyClose
        latest_close = DailyClose.objects.filter(business_date=day, is_finalized=True).first()
    except Exception:
        latest_close = None
    return {
        'opening_cash_syp': opening_cash,
        'non_sales_cash_in_syp': cash_in,
        'cash_out_syp': cash_out_movements,
        'cash_expenses_syp': cash_expenses,
        'expected_cash_syp': max(expected, 0),
        'actual_cash_counted_syp': latest_close.actual_cash_counted_syp if latest_close else None,
        'cash_difference_syp': latest_close.cash_difference_syp if latest_close else None,
        'expenses_total_syp': expenses.exclude(status=Expense.Status.CANCELLED).aggregate(v=Sum('amount_syp'))['v'] or 0,
        'unpaid_expenses_syp': expenses.filter(status__in=[Expense.Status.DRAFT, Expense.Status.APPROVED]).aggregate(v=Sum('amount_syp'))['v'] or 0,
        'cancelled_expenses_syp': expenses.filter(status=Expense.Status.CANCELLED).aggregate(v=Sum('amount_syp'))['v'] or 0,
        'expenses_by_category': expenses.exclude(status=Expense.Status.CANCELLED).values('category__name_ar').annotate(total=Sum('amount_syp')).order_by('-total'),
        'movements': movements.select_related('created_by','vendor','related_expense')[:100],
        'unpaid_expenses': expenses.filter(status__in=[Expense.Status.DRAFT, Expense.Status.APPROVED]).select_related('category','vendor')[:50],
        'cancelled_expenses': expenses.filter(status=Expense.Status.CANCELLED).select_related('category','vendor')[:50],
    }

def sync_cash_expense_movement(expense, user=None):
    if not expense.affects_cashbox():
        return None
    movement, _ = CashMovement.objects.update_or_create(
        related_expense=expense,
        defaults={
            'business_date': expense.business_date,
            'movement_type': CashMovement.MovementType.CASH_EXPENSE,
            'direction': CashMovement.Direction.OUT,
            'amount_syp': expense.amount_syp,
            'vendor': expense.vendor,
            'title': expense.title,
            'notes': expense.description,
            'created_by': user or expense.created_by,
        },
    )
    return movement

from decimal import Decimal
from django.db import transaction
from django.utils import timezone


def current_business_date():
    return timezone.localdate()


def close_snapshot(close):
    fields = ['opening_cash_syp','cash_sales_syp','non_cash_sales_syp','total_payments_syp','unpaid_orders_syp','partial_payments_syp','discounts_syp','cancelled_orders_syp','refunds_or_reversals_syp','expected_cash_syp','actual_cash_counted_syp','cash_difference_syp','notes','status','closed_at','reopened_at','reopen_reason']
    data = {f: getattr(close, f) for f in fields}
    for k, v in list(data.items()):
        if hasattr(v, 'isoformat'):
            data[k] = v.isoformat()
    data['closed_by_id'] = close.closed_by_id
    data['reopened_by_id'] = close.reopened_by_id
    return data


def purchase_totals_for_date(day):
    from core.models import Purchase
    qs = Purchase.objects.filter(business_date=day).exclude(status=Purchase.Status.CANCELLED)
    return qs.aggregate(total=Sum('total_syp'), paid=Sum('amount_paid_syp'))


def build_close_values(day, actual_cash_counted_syp=0, notes='', opening_cash_syp=None):
    from core.views_legacy import _build_day_report
    _rows, sums = _build_day_report(day)
    if opening_cash_syp is None:
        opening_cash_syp = sums.get('opening_cash_syp') or 0
    purchases = purchase_totals_for_date(day).get('total') or Decimal('0')
    expected = int(opening_cash_syp) + int(sums.get('cash_total') or 0) + int(sums.get('non_sales_cash_in_syp') or 0) - int(sums.get('cash_out_syp') or 0) - int(sums.get('cash_expenses_syp') or 0)
    actual = int(actual_cash_counted_syp or 0)
    return {
        'opening_cash_syp': int(opening_cash_syp or 0),
        'cash_sales_syp': int(sums.get('cash_total') or 0),
        'non_cash_sales_syp': int(sums.get('non_cash_sales_syp') or 0),
        'total_payments_syp': int(sums.get('paid_total') or 0),
        'unpaid_orders_syp': int(sums.get('remaining_total') or 0),
        'partial_payments_syp': int(sums.get('partial_payments_syp') or 0),
        'discounts_syp': int(sums.get('discounts_syp') or 0),
        'cancelled_orders_syp': int(sums.get('cancelled_value') or 0),
        'refunds_or_reversals_syp': 0,
        'expected_cash_syp': max(expected, 0),
        'actual_cash_counted_syp': actual,
        'cash_difference_syp': actual - max(expected, 0),
        'notes': notes,
    }


def finalize_daily_close(day, user, actual_cash_counted_syp, notes='', opening_cash_syp=None):
    from core.models import ActivityLog, DailyClose, DailyCloseRevision
    with transaction.atomic():
        close, created = DailyClose.objects.select_for_update().get_or_create(business_date=day, defaults={'status': DailyClose.Status.OPEN, 'is_finalized': True})
        if close.status == DailyClose.Status.CLOSED and close.closed_at:
            return close, False
        if not created:
            DailyCloseRevision.objects.create(daily_close=close, revision_type='before_reclose', snapshot=close_snapshot(close), created_by=user)
        values = build_close_values(day, actual_cash_counted_syp, notes, opening_cash_syp)
        for k,v in values.items(): setattr(close,k,v)
        close.status = DailyClose.Status.CLOSED; close.is_finalized=True; close.closed_by=user; close.closed_at=timezone.now()
        close.full_clean(); close.save()
        DailyCloseRevision.objects.create(daily_close=close, revision_type='closed', snapshot=close_snapshot(close), created_by=user)
        ActivityLog.objects.create(actor=user, action='daily_close_closed', details={'daily_close_id': close.id, 'business_date': day.isoformat()})
        return close, True


def reopen_daily_close(close, user, reason):
    from core.models import ActivityLog, DailyClose, DailyCloseRevision
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('سبب إعادة الفتح مطلوب.')
    with transaction.atomic():
        close = DailyClose.objects.select_for_update().get(pk=close.pk)
        DailyCloseRevision.objects.create(daily_close=close, revision_type='before_reopen', snapshot=close_snapshot(close), reason=reason, created_by=user)
        close.status = DailyClose.Status.REOPENED; close.reopened_by=user; close.reopened_at=timezone.now(); close.reopen_reason=reason
        close.save(update_fields=['status','reopened_by','reopened_at','reopen_reason','updated_at'])
        ActivityLog.objects.create(actor=user, action='daily_close_reopened', details={'daily_close_id': close.id, 'business_date': close.business_date.isoformat(), 'reason': reason})
        return close
