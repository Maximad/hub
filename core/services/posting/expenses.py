from django.utils import timezone

from core.models import ActivityLog, CashMovement, Expense
from .engine import dispatch
from .exceptions import InvalidTransition


def _audit(context, action, expense):
    ActivityLog.objects.create(actor=context.actor, action=action, details={'expense_id': expense.pk, 'posting_key': context.idempotency_key})


def _sync_cash(expense, context):
    if expense.affects_cashbox():
        CashMovement.objects.select_for_update().update_or_create(
            related_expense=expense,
            defaults={'business_date': expense.business_date, 'movement_type': CashMovement.MovementType.CASH_EXPENSE,
                      'direction': CashMovement.Direction.OUT, 'amount_syp': expense.amount_syp, 'vendor': expense.vendor,
                      'title': expense.title, 'notes': expense.description, 'created_by': context.actor, 'approved_by': context.approver},
        )


def create(expense, context):
    def handle(source):
        source.created_by = source.created_by or context.actor
        source.full_clean(); source.save()
        if source.status == Expense.Status.APPROVED:
            source.approved_by=context.approver or context.actor; source.approved_at=timezone.now(); source.save()
        elif source.status == Expense.Status.PAID:
            source.paid_by=context.actor; source.paid_at=timezone.now(); source.save()
        _sync_cash(source, context); _audit(context, 'expense_posted', source)
        return source
    # Unsaved sources cannot be locked, but the command receipt serializes creation.
    return dispatch('expense.create', expense, context, handle)


def approve(expense, context):
    def handle(source):
        if source.status != Expense.Status.DRAFT: raise InvalidTransition('يمكن اعتماد المسودة فقط.')
        source.status=Expense.Status.APPROVED; source.approved_by=context.approver or context.actor; source.approved_at=timezone.now(); source.save()
        _audit(context, 'expense_approved', source); return source
    return dispatch('expense.approve', expense, context, handle)


def pay(expense, context, payment_method, paid_from):
    def handle(source):
        if source.status not in {Expense.Status.DRAFT, Expense.Status.APPROVED}: raise InvalidTransition('المصروف غير قابل للدفع.')
        source.status=Expense.Status.PAID; source.payment_method=payment_method; source.paid_from=paid_from
        source.paid_by=context.actor; source.paid_at=timezone.now(); source.full_clean(); source.save()
        _sync_cash(source, context); _audit(context, 'expense_paid', source); return source
    return dispatch('expense.pay', expense, context, handle)


def cancel(expense, context, reason):
    def handle(source):
        if not reason.strip(): raise InvalidTransition('سبب الإلغاء مطلوب.')
        source.status=Expense.Status.CANCELLED; source.cancellation_reason=reason; source.save()
        source.cash_movements.filter(is_cancelled=False).update(is_cancelled=True, cancellation_reason=reason)
        _audit(context, 'expense_cancelled', source); return source
    return dispatch('expense.cancel', expense, context, handle)


def reverse(expense, context, reason):
    return cancel(expense, context, reason)


def post_cash_movement(movement, context):
    def handle(source):
        source.created_by=source.created_by or context.actor; source.approved_by=context.approver
        source.full_clean(); source.save()
        ActivityLog.objects.create(actor=context.actor,action='cash_movement_posted',details={'cash_movement_id':source.pk,'posting_key':context.idempotency_key})
        return source
    return dispatch('cash_movement.post',movement,context,handle)
