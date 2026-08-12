from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.db.models import Sum

from core.models import (ActivityLog, CashMovement, FinancialAccount, Payment,
                         PostingBatch, PostingEntry)
from .engine import dispatch
from .exceptions import InvalidTransition
from .policy import require_active


def collect(order, context, amount, method, notes=''):
    def handle(source):
        # ``dispatch`` holds an order row lock, so concurrent collectors cannot both
        # validate against the same remaining balance.
        paid = source.payments.filter(is_active=True, is_reversed=False).exclude(method=Payment.Method.UNPAID).aggregate(total=Sum('amount_syp'))['total'] or 0
        if amount <= 0:
            raise InvalidTransition('مبلغ الدفعة يجب أن يكون موجباً.')
        if paid + amount > source.total_syp:
            raise InvalidTransition('المبلغ لا يجوز أن يتجاوز المتبقي على الطلب.')
        if method in {Payment.Method.UNPAID, Payment.Method.FREE, Payment.Method.MEMBER_DISCOUNT}:
            raise InvalidTransition('هذه الطريقة ليست دفعة محصلة.')
        payment=Payment(order=source, amount_syp=amount, method=method, notes=notes, created_by=context.actor)
        payment.full_clean(); payment.save()
        revenue = FinancialAccount.objects.filter(
            account_type=FinancialAccount.AccountType.REVENUE, is_active=True,
        ).order_by('pk').first()
        if not revenue:
            raise InvalidTransition('لا يوجد حساب إيراد مالي نشط.')
        require_active(revenue)
        if method == Payment.Method.CASH:
            account = FinancialAccount.objects.filter(
                scope='cashbox', is_active=True,
            ).order_by('pk').first()
            if not account:
                raise InvalidTransition('لا يوجد حساب صندوق مالي نشط لاستلام الدفعة النقدية.')
            require_active(account)
            CashMovement.objects.create(business_date=context.date_for(source), movement_type=CashMovement.MovementType.OTHER,
                direction=CashMovement.Direction.IN, amount_syp=amount, related_order=source, related_payment=payment,
                financial_account=account, is_generated=True,
                title=f'دفع {source.display_number}', created_by=context.actor, approved_by=context.approver)
        else:
            account = FinancialAccount.objects.filter(
                account_type=FinancialAccount.AccountType.CLEARING, is_active=True,
            ).order_by('pk').first()
            if not account:
                raise InvalidTransition('لا يوجد حساب مقاصة مالي نشط.')
            require_active(account)
        batch = PostingBatch.objects.create(
            operation_type='order_payment.collect',
            source_content_type=ContentType.objects.get_for_model(payment),
            source_object_id=str(payment.pk), business_date=context.date_for(source),
            status=PostingBatch.Status.POSTED,
            idempotency_key=f'{context.idempotency_key}:batch', actor=context.actor,
            approver=context.approver, posted_at=timezone.now(), channel=context.channel,
            metadata=dict(context.request_metadata),
        )
        PostingEntry.objects.bulk_create([
            PostingEntry(batch=batch, account=account, debit=amount,
                         description=f'تحصيل {source.display_number}'),
            PostingEntry(batch=batch, account=revenue, credit=amount,
                         description=f'إيراد {source.display_number}'),
        ])
        ActivityLog.objects.create(actor=context.actor, action='payment_added', details={'payment_id':payment.pk,'posting_key':context.idempotency_key})
        return payment
    return dispatch('order_payment.collect', order, context, handle)


def reverse(payment, context, reason):
    def handle(source):
        if source.is_reversed: raise InvalidTransition('الدفعة معكوسة مسبقاً.')
        if not (reason or '').strip(): raise InvalidTransition('سبب عكس الدفعة مطلوب.')
        source.is_reversed=True; source.is_active=False; source.reversal_reason=reason; source.reversed_by=context.actor; source.reversed_at=timezone.now(); source.full_clean(); source.save()
        original = source.cash_movements.filter(is_cancelled=False, direction=CashMovement.Direction.IN).first()
        if original:
            CashMovement.objects.create(
                business_date=context.date_for(source.order), financial_account=original.financial_account,
                movement_type=CashMovement.MovementType.REFUND, direction=CashMovement.Direction.OUT,
                amount_syp=source.amount_syp, related_order=source.order, related_payment=source,
                title=f'عكس دفع {source.order.display_number}', notes=reason,
                created_by=context.actor, approved_by=context.approver, is_generated=True,
            )
        ActivityLog.objects.create(actor=context.actor, action='payment_reversed', details={'payment_id':source.pk,'posting_key':context.idempotency_key})
        return source
    return dispatch('order_payment.reverse', payment, context, handle)
