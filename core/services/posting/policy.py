"""Launch finance authorization policy (D01-D04 and D12-D14 only)."""
from accounts.permissions import is_owner_or_admin
from .exceptions import InvalidTransition


def is_finance_user(user):
    """Finance is represented by the existing finance permissions, not a new role."""
    return bool(user and user.is_authenticated and user.is_active and (
        is_owner_or_admin(user) or user.has_perm('core.close_business_day')
    ))


def require_finance_actor(context, operation='العملية المالية'):
    if not is_finance_user(context.actor):
        raise InvalidTransition(f'{operation} متاحة للإدارة أو المالية فقط.')


def require_permitted_approval(context, operation='العملية المالية'):
    if not context.actor or not context.actor.is_authenticated or not context.actor.is_active:
        raise InvalidTransition(f'منفذ {operation} مطلوب.')
    approver = context.approver or context.actor
    if not is_finance_user(approver):
        raise InvalidTransition('المعتمد يجب أن يكون من الإدارة أو المالية.')
    if approver.pk == context.actor.pk and not is_finance_user(context.actor):
        raise InvalidTransition('لا يجوز لغير الإدارة أو المالية اعتماد عمليته بنفسه.')
    return approver


def require_active(account):
    if not account or not account.is_active:
        raise InvalidTransition('الحساب المالي المحدد غير فعّال.')
    return account
