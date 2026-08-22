from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, HubVisit, Order, Payment
from core.services.posting.engine import dispatch
from core.services.posting.order_payments import collect as collect_order_payment


COLLECTIBLE_PAYMENT_METHODS = (
    (Payment.Method.CASH, Payment.Method.CASH.label),
    (Payment.Method.MANUAL_TRANSFER, Payment.Method.MANUAL_TRANSFER.label),
)


def active_visit_orders(visit, *, for_update=False):
    qs = (
        Order.objects.filter(visit=visit)
        .exclude(status=Order.Status.CANCELLED)
        .select_related('table', 'table__room')
        .prefetch_related('items', 'payments', 'discounts')
        .order_by('created_at', 'pk')
    )
    if for_update:
        qs = qs.select_for_update(of=('self',))
    return qs


def visit_financials(visit, *, orders=None):
    rows = list(orders if orders is not None else active_visit_orders(visit))
    total = sum(order.total_syp for order in rows)
    paid = sum(order.paid_syp for order in rows)
    remaining = max(total - paid, 0)
    if total == 0:
        label = 'ضيافة'
    elif remaining == 0:
        label = 'مدفوع'
    elif paid > 0:
        label = 'مدفوع جزئياً'
    else:
        label = 'غير مدفوع'
    return {
        'orders': rows,
        'total': total,
        'paid': paid,
        'remaining': remaining,
        'payment_label': label,
    }


def allocate_visit_payment(visit, context, amount_syp, method, notes=''):
    """Collect one visit payment and allocate it across unpaid orders.

    The outer PostingCommand is the idempotency boundary for the combined
    cashier action. Child order payments get deterministic derived keys.
    Orders remain the accounting documents; HubVisit remains an operational
    umbrella rather than a ledger.
    """
    amount = Decimal(str(amount_syp))
    if amount <= 0:
        raise ValidationError('مبلغ الدفعة يجب أن يكون موجباً.')
    if method not in dict(COLLECTIBLE_PAYMENT_METHODS):
        raise ValidationError('طريقة الدفع المختارة غير قابلة للتحصيل.')

    allocations = []

    def handle(locked_visit):
        orders = list(active_visit_orders(locked_visit, for_update=True))
        financial = visit_financials(locked_visit, orders=orders)
        if amount > Decimal(str(financial['remaining'])):
            raise ValidationError('المبلغ لا يجوز أن يتجاوز الرصيد المتبقي على الجلسة.')

        left = amount
        for order in orders:
            if left <= 0:
                break
            remaining = Decimal(str(order.remaining_syp))
            if remaining <= 0:
                continue
            allocation = min(left, remaining)
            payment = collect_order_payment(
                order,
                context.with_key_suffix(f'order-{order.pk}'),
                allocation,
                method,
                notes,
            )
            allocations.append((order, payment, allocation))
            left -= allocation

        if left != 0:
            raise ValidationError('تعذر توزيع كامل مبلغ الدفعة على طلبات الجلسة.')

        locked_visit.last_activity_at = timezone.now()
        locked_visit.save(update_fields=['last_activity_at', 'updated_at'])
        ActivityLog.objects.create(
            actor=context.actor,
            action='visit_payment_allocated',
            details={
                'visit_id': locked_visit.pk,
                'amount_syp': str(amount),
                'method': method,
                'posting_key': context.idempotency_key,
                'allocations': [
                    {
                        'order_id': order.pk,
                        'payment_id': payment.pk,
                        'amount_syp': str(allocation),
                    }
                    for order, payment, allocation in allocations
                ],
            },
        )
        return locked_visit

    with transaction.atomic():
        result = dispatch('visit_payment.collect', visit, context, handle)
    return result, allocations
