from django.db.models import Sum
from django.utils import timezone
from core.models import CashMovement, Expense, Payment


def finance_summary_for_date(day, base_sums=None):
    base_sums = base_sums or {}
    expenses = Expense.objects.filter(business_date=day)
    movements = CashMovement.objects.filter(business_date=day, is_cancelled=False)
    opening_cash = movements.filter(movement_type=CashMovement.MovementType.OPENING_CASH, direction=CashMovement.Direction.IN).aggregate(v=Sum('amount_syp'))['v'] or 0
    cash_in = movements.filter(direction=CashMovement.Direction.IN).exclude(movement_type=CashMovement.MovementType.OPENING_CASH).aggregate(v=Sum('amount_syp'))['v'] or 0
    cash_out_movements = movements.filter(direction=CashMovement.Direction.OUT).exclude(related_expense__isnull=False).aggregate(v=Sum('amount_syp'))['v'] or 0
    # The generated projection is the single source used by cash screens and
    # closing, including expenses whose legacy ``paid_from`` value is stale.
    cash_expenses = movements.filter(
        related_expense__isnull=False, is_generated=True,
        movement_type=CashMovement.MovementType.CASH_EXPENSE,
        direction=CashMovement.Direction.OUT,
    ).aggregate(v=Sum('amount_syp'))['v'] or 0
    latest_close = None
    try:
        from core.models import DailyClose
        latest_close = DailyClose.objects.filter(business_date=day, is_finalized=True).select_related('account').first()
    except Exception:
        latest_close = None
    expected = 0
    if latest_close and latest_close.account_id:
        from core.services.posting.closing import close_totals
        ledger = close_totals(latest_close.account, day)
        expected = (latest_close.opening_cash_syp + ledger['cash_receipts'] + ledger['transfers_in']
                    + ledger['reversal_inflows'] - ledger['cash_payments']
                    - ledger['transfers_out'] - ledger['refunds_or_reversals'])
    return {
        'opening_cash_syp': opening_cash,
        'non_sales_cash_in_syp': cash_in,
        'cash_out_syp': cash_out_movements,
        'cash_expenses_syp': cash_expenses,
        'expected_cash_syp': expected,
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
    purchases = list(qs)
    return {'total': sum((p.total_syp for p in purchases), 0), 'paid': sum((p.amount_paid_syp for p in purchases), 0)}


def build_close_values(day, actual_cash_counted_syp=0, notes='', opening_cash_syp=None):
    """Deprecated read facade using the same posted-ledger calculation as closes."""
    from core.models import FinancialAccount
    from core.services.posting.closing import close_totals
    account = FinancialAccount.objects.filter(scope='cashbox', is_active=True).order_by('pk').first()
    totals = close_totals(account, day) if account else {key: Decimal('0') for key in
        ('cash_receipts', 'transfers_in', 'reversal_inflows', 'cash_payments',
         'transfers_out', 'refunds_or_reversals')}
    if opening_cash_syp is None:
        opening_cash_syp = 0
    expected = (Decimal(opening_cash_syp) + totals['cash_receipts'] + totals['transfers_in']
                + totals['reversal_inflows'] - totals['cash_payments']
                - totals['transfers_out'] - totals['refunds_or_reversals'])
    actual = int(actual_cash_counted_syp or 0)
    return {
        'opening_cash_syp': int(opening_cash_syp or 0),
        'cash_sales_syp': int(totals['cash_receipts']), 'non_cash_sales_syp': 0,
        'total_payments_syp': int(totals['cash_receipts']), 'unpaid_orders_syp': 0,
        'partial_payments_syp': 0, 'discounts_syp': 0, 'cancelled_orders_syp': 0,
        'refunds_or_reversals_syp': int(totals['refunds_or_reversals']),
        'expected_cash_syp': int(expected),
        'actual_cash_counted_syp': actual,
        'cash_difference_syp': actual - int(expected),
        'notes': notes,
    }


def finalize_daily_close(day, user, actual_cash_counted_syp, notes='', opening_cash_syp=None):
    """Compatibility facade; all close writes are owned by posting.closing."""
    from core.models import DailyClose, FinancialAccount
    from core.services.posting import closing
    from core.services.posting.context import PostingContext
    with transaction.atomic():
        account = FinancialAccount.objects.select_for_update().filter(
            scope='cashbox', is_active=True,
        ).order_by('pk').first()
        if account is None:
            account, _ = FinancialAccount.objects.get_or_create(code='cash:default', defaults={
                'name_ar': 'الصندوق', 'name_en': 'Default cashbox', 'account_type': FinancialAccount.AccountType.ASSET,
                'scope': 'cashbox', 'is_active': True, 'negative_balance_policy': FinancialAccount.NegativeBalancePolicy.ALLOW})
            account = FinancialAccount.objects.select_for_update().get(pk=account.pk)
        closes = DailyClose.objects.select_for_update().filter(account=account, business_date=day)
        close = closes.filter(
            status__in=[DailyClose.Status.OPEN, DailyClose.Status.REOPENED],
        ).order_by('pk').first()
        if close is None:
            close = closes.filter(
                status=DailyClose.Status.CLOSED, is_finalized=True,
            ).order_by('pk').first()
        if close is None:
            close = DailyClose.objects.create(
                account=account, business_date=day, status=DailyClose.Status.OPEN,
                is_finalized=False,
            )
        if close.status == DailyClose.Status.CLOSED and close.closed_at:
            return close, False
        context = PostingContext(actor=user, approver=user, business_date=day,
                                 idempotency_key=f'compat-close:{account.pk}:{day}:{close.pk}')
        return closing.close(close, context, actual_cash_counted_syp, notes, opening_cash_syp), True


def reopen_daily_close(close, user, reason):
    from core.services.posting import closing
    from core.services.posting.context import PostingContext
    if not (reason or '').strip():
        raise ValueError('سبب إعادة الفتح مطلوب.')
    context = PostingContext(actor=user, approver=user, business_date=close.business_date,
                             idempotency_key=f'compat-reopen:{close.pk}:{close.updated_at.isoformat()}')
    return closing.reopen(close, context, reason)
