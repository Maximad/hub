from django.utils import timezone
from django.db.models import Sum

from core.models import ActivityLog, CashMovement, Payment
from .engine import dispatch
from .exceptions import InvalidTransition


def collect(order, context, amount, method, notes=''):
    def handle(source):
        # ``dispatch`` holds an order row lock, so concurrent collectors cannot both
        # validate against the same remaining balance.
        paid = source.payments.filter(is_active=True, is_reversed=False).exclude(method=Payment.Method.UNPAID).aggregate(total=Sum('amount_syp'))['total'] or 0
        if amount <= 0:
            raise InvalidTransition('مبلغ الدفعة يجب أن يكون موجباً.')
        if paid + amount > source.total_syp:
            raise InvalidTransition('المبلغ لا يجوز أن يتجاوز المتبقي على الطلب.')
        payment=Payment(order=source, amount_syp=amount, method=method, notes=notes, created_by=context.actor)
        payment.full_clean(); payment.save()
        if method == Payment.Method.CASH:
            CashMovement.objects.create(business_date=context.date_for(source), movement_type=CashMovement.MovementType.OTHER,
                direction=CashMovement.Direction.IN, amount_syp=amount, related_order=source, related_payment=payment,
                title=f'دفع {source.display_number}', created_by=context.actor, approved_by=context.approver)
        ActivityLog.objects.create(actor=context.actor, action='payment_added', details={'payment_id':payment.pk,'posting_key':context.idempotency_key})
        return payment
    return dispatch('order_payment.collect', order, context, handle)


def reverse(payment, context, reason):
    def handle(source):
        if source.is_reversed: raise InvalidTransition('الدفعة معكوسة مسبقاً.')
        source.is_reversed=True; source.is_active=False; source.reversal_reason=reason; source.reversed_by=context.actor; source.reversed_at=timezone.now(); source.full_clean(); source.save()
        source.cash_movements.update(is_cancelled=True, cancellation_reason=reason)
        ActivityLog.objects.create(actor=context.actor, action='payment_reversed', details={'payment_id':source.pk,'posting_key':context.idempotency_key})
        return source
    return dispatch('order_payment.reverse', payment, context, handle)
