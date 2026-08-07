"""Thin import/API adapter for the authoritative expense posting commands."""

from django.db import transaction

from core.models import Expense, ExpenseCategory, FinancialAccount
from . import expenses
from .exceptions import InvalidTransition


def _required(payload, name):
    value = payload.get(name)
    if value in (None, ''):
        raise InvalidTransition(f'حقل الاستيراد مطلوب: {name}.')
    return value


@transaction.atomic
def import_expense(payload, context, *, account_mapping):
    """Validate one external row and delegate every write to the expense service.

    ``account_mapping`` maps an external account reference to a stable account
    code. It is intentionally mandatory; names and business units are never
    guessed by this boundary.
    """
    status = payload.get('status', Expense.Status.DRAFT)
    account = None
    if status == Expense.Status.PAID:
        external_ref = _required(payload, 'account_ref')
        try:
            account_code = account_mapping[external_ref]
        except KeyError as error:
            raise InvalidTransition(f'لا يوجد تعيين صريح للحساب الخارجي: {external_ref}.') from error
        try:
            account = FinancialAccount.objects.get(code=account_code, is_active=True)
        except FinancialAccount.DoesNotExist as error:
            raise InvalidTransition(f'الحساب المعيّن غير موجود أو غير نشط: {account_code}.') from error

    try:
        category = ExpenseCategory.objects.get(code=_required(payload, 'category_code'), is_active=True)
    except ExpenseCategory.DoesNotExist as error:
        raise InvalidTransition('تصنيف المصروف غير موجود أو غير نشط.') from error

    expense = Expense(
        business_date=_required(payload, 'business_date'), category=category,
        payee_type=payload.get('payee_type', Expense.PayeeType.MANUAL),
        supplier_name=payload.get('supplier_name', '').strip(),
        title=_required(payload, 'title'), description=payload.get('description', ''),
        amount_syp=_required(payload, 'amount_syp'), payment_method=payload.get('payment_method', ''),
        paid_from=payload.get('paid_from', Expense.PaidFrom.UNPAID), financial_account=account,
        receipt_number=payload.get('receipt_number', ''), status=Expense.Status.DRAFT,
    )
    draft_context = context.with_key_suffix('draft')
    expense = expenses.create_draft(expense, draft_context)
    if status == Expense.Status.PAID:
        return expenses.pay_immediately(expense, context.with_key_suffix('payment'), account, expense.payment_method)
    if status != Expense.Status.DRAFT:
        raise InvalidTransition('يدعم محول الاستيراد حالياً المسودة أو الدفع الفوري فقط.')
    return expense
