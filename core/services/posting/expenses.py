from dataclasses import replace

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import ActivityLog, CashMovement, Expense, FinancialAccount, PostingBatch
from .engine import dispatch
from .exceptions import InvalidTransition
from .policy import require_active, require_finance_actor, require_permitted_approval


def _audit(context, action, expense):
    ActivityLog.objects.create(actor=context.actor, action=action,
        details={'expense_id': expense.pk, 'posting_key': context.idempotency_key})


def _batch(expense, context, operation, *, reversal_of=None):
    return PostingBatch.objects.create(
        operation_type=operation, source_content_type=ContentType.objects.get_for_model(expense),
        source_object_id=str(expense.pk), business_date=expense.business_date,
        status=PostingBatch.Status.POSTED, idempotency_key=f'{context.idempotency_key}:batch',
        actor=context.actor, approver=context.approver, posted_at=timezone.now(),
        reversal_of=reversal_of, channel=context.channel, metadata=dict(context.request_metadata),
    )


def _sync_cash(expense, context, reason='لم يعد المصروف مدفوعاً نقداً من الصندوق.'):
    active = expense.cash_movements.select_for_update().filter(is_generated=True, is_cancelled=False)
    if not expense.affects_cashbox():
        active.update(is_cancelled=True, cancellation_reason=reason)
        return
    movement, _ = CashMovement.objects.update_or_create(
        related_expense=expense, is_generated=True, is_cancelled=False,
        defaults={'business_date': expense.business_date, 'financial_account': expense.financial_account,
                  'movement_type': CashMovement.MovementType.CASH_EXPENSE,
                  'direction': CashMovement.Direction.OUT, 'amount_syp': expense.amount_syp, 'vendor': expense.vendor,
                  'title': expense.title, 'notes': expense.description, 'created_by': context.actor,
                  'approved_by': context.approver},
    )
    return movement


def _validate_payee(source):
    if source.payee_type == Expense.PayeeType.VENDOR and not source.vendor_id:
        raise InvalidTransition('يجب اختيار المورد المستفيد.')
    if source.payee_type == Expense.PayeeType.MANUAL and not source.supplier_name.strip():
        raise InvalidTransition('اسم المستفيد مطلوب.')


def create_draft(expense, context):
    def handle(source):
        if source.status != Expense.Status.DRAFT:
            raise InvalidTransition('يجب إنشاء المصروف كمسودة.')
        _validate_payee(source)
        source.created_by = source.created_by or context.actor
        source.posting_state = Expense.PostingState.DRAFT
        source.full_clean(); source.save(); _audit(context, 'expense_draft_created', source)
        return source
    return dispatch('expense.create_draft', expense, context, handle)


def approve_liability(expense, context, liability_account=None):
    def handle(source):
        account = liability_account or source.liability_account
        if source.status != Expense.Status.DRAFT or not account or account.account_type != FinancialAccount.AccountType.LIABILITY:
            raise InvalidTransition('اعتماد الالتزام يتطلب مسودة وحساب التزام.')
        source.status = Expense.Status.APPROVED; source.liability_account = account
        source.approved_by = context.approver or context.actor; source.approved_at = timezone.now()
        source.posting_state = Expense.PostingState.POSTED; source.posting_version += 1
        source.save(); source.approval_batch = _batch(source, context, 'expense.approve_liability')
        source.save(update_fields=['approval_batch', 'updated_at']); _audit(context, 'expense_liability_approved', source)
        return source
    return dispatch('expense.approve_liability', expense, context, handle)


def pay_immediately(expense, context, financial_account, payment_method):
    approver = require_permitted_approval(context, 'دفع المصروف الفوري')
    if context.approver is None:
        context = replace(context, approver=approver)
    def handle(source):
        require_active(financial_account)
        if source.status != Expense.Status.DRAFT or not financial_account:
            raise InvalidTransition('يمكن الدفع الفوري لمسودة ومن حساب مالي محدد فقط.')
        source.status = Expense.Status.PAID; source.payment_method = payment_method
        source.financial_account = financial_account; source.paid_by = context.actor; source.paid_at = timezone.now()
        source.posting_state = Expense.PostingState.POSTED; source.posting_version += 1
        source.full_clean(); source.save(); source.payment_batch = _batch(source, context, 'expense.pay_immediately')
        source.save(update_fields=['payment_batch', 'updated_at']); _sync_cash(source, context)
        _audit(context, 'expense_paid_immediately', source); return source
    return dispatch('expense.pay_immediately', expense, context, handle)


def settle_liability(expense, context, financial_account, payment_method=None):
    def handle(source):
        if source.status != Expense.Status.APPROVED or not source.liability_account_id or not financial_account:
            raise InvalidTransition('يمكن تسوية التزام معتمد فقط ومن حساب مالي محدد.')
        source.status = Expense.Status.PAID; source.financial_account = financial_account
        source.payment_method = payment_method or source.payment_method; source.paid_by = context.actor; source.paid_at = timezone.now()
        source.posting_version += 1; source.full_clean(); source.save()
        source.payment_batch = _batch(source, context, 'expense.settle_liability')
        source.save(update_fields=['payment_batch', 'updated_at']); _sync_cash(source, context)
        _audit(context, 'expense_liability_settled', source); return source
    return dispatch('expense.settle_liability', expense, context, handle)


def cancel_unposted_draft(expense, context, reason):
    def handle(source):
        if source.status != Expense.Status.DRAFT or source.posting_state != Expense.PostingState.DRAFT or not reason.strip():
            raise InvalidTransition('يمكن إلغاء مسودة غير مرحلة فقط مع ذكر السبب.')
        source.status = Expense.Status.CANCELLED; source.posting_state = Expense.PostingState.CANCELLED
        source.cancellation_reason = reason; source.save(); _sync_cash(source, context, reason)
        _audit(context, 'expense_draft_cancelled', source); return source
    return dispatch('expense.cancel_unposted_draft', expense, context, handle)


def reverse_posted_expense(expense, context, reason):
    def handle(source):
        require_permitted_approval(context, 'عكس المصروف')
        if source.posting_state != Expense.PostingState.POSTED or not reason.strip():
            raise InvalidTransition('يمكن عكس مصروف مرحل فقط مع ذكر السبب.')
        original = source.payment_batch or source.approval_batch
        source.status = Expense.Status.CANCELLED; source.posting_state = Expense.PostingState.REVERSED
        source.cancellation_reason = reason; source.posting_version += 1
        source.save(); source.reversal_batch = _batch(source, context, 'expense.reverse', reversal_of=original)
        source.save(update_fields=['reversal_batch', 'updated_at']); _sync_cash(source, context, reason)
        _audit(context, 'expense_reversed', source); return source
    return dispatch('expense.reverse', expense, context, handle)


# Transitional aliases. New integrations should use the explicit commands above.
create = create_draft
approve = approve_liability
cancel = cancel_unposted_draft
reverse = reverse_posted_expense


def pay(expense, context, payment_method, paid_from=None, financial_account=None):
    account = financial_account or expense.financial_account
    if not account:
        raise InvalidTransition('financial_account مطلوب؛ paid_from حقل توافق قديم فقط.')
    return pay_immediately(expense, context, account, payment_method)


def post_cash_movement(movement, context):
    require_finance_actor(context, 'حركة الصندوق')
    approver = require_permitted_approval(context, 'حركة الصندوق')
    if context.approver is None:
        context = replace(context, approver=approver)
    def handle(source):
        if not (source.notes or source.title or '').strip():
            raise InvalidTransition('سبب حركة الصندوق مطلوب.')
        require_active(source.financial_account)
        source.approved_by = approver
        source.created_by=source.created_by or context.actor; source.approved_by=context.approver
        source.full_clean(); source.save()
        ActivityLog.objects.create(actor=context.actor,action='cash_movement_posted',details={'cash_movement_id':source.pk,'posting_key':context.idempotency_key})
        return source
    return dispatch('cash_movement.post',movement,context,handle)
