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
