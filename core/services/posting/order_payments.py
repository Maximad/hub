from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.db.models import Sum

from core.models import (ActivityLog, CashMovement, CancellationReason, FinancialAccount, Payment,
                         PostingBatch, PostingEntry)
from .engine import dispatch, ensure_period_open
from .exceptions import InvalidTransition
from .policy import require_active, require_finance_actor, require_permitted_approval
from .ledger import post_balanced_batch


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


def reverse(payment, context, reason, *, reason_code=CancellationReason.OTHER):
    """D03: authorize and post one linked equal-and-opposite customer payment reversal.

    Only reverses verified collections made by `collect()`. Legacy/manual
    payments without a posted source journal require finance reconciliation.
    Exactly-once receipt is owned by `dispatch`, with the payment row locked.
    """
    require_finance_actor(context, 'عكس دفعة الزبون')
    approver = require_permitted_approval(context, 'عكس دفعة الزبون')
    if context.approver is not None and context.approver.pk != approver.pk:
        raise InvalidTransition('اعتماد الدفعة غير متطابق.')
    reason = (reason or '').strip()
    if not 3 <= len(reason) <= 2000:
        raise InvalidTransition('سبب عكس الدفعة مطلوب (3 أحرف على الأقل).')
    if reason_code not in CancellationReason.values:
        raise InvalidTransition('صنف سبب عكس الدفعة غير صالح.')
    refund_reference = str(context.request_metadata.get('refund_reference') or '').strip()
    if len(refund_reference) < 3 or len(refund_reference) > 240:
        raise InvalidTransition('إثبات إعادة المبلغ مطلوب (مرجع تحويل أو محضر تسليم نقدي).')
    if context.request_metadata.get('refund_completed') is not True:
        raise InvalidTransition('يلزم تأكيد تنفيذ إعادة المبلغ قبل تسجيل عكس الدفعة.')

    def handle(source):
        if source.is_reversed or not source.is_active or source.reversed_at:
            raise InvalidTransition('الدفعة معكوسة مسبقاً أو غير فعّالة.')
        if source.method not in {Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER}:
            raise InvalidTransition('طريقة الدفعة ليست تحصيلاً مالياً قابلاً للعكس.')
        if not source.order_id or source.amount_syp <= 0:
            raise InvalidTransition('لا يوجد طلب أو مبلغ تحصيل صالح.')
        content_type = ContentType.objects.get_for_model(Payment)
        originals = list(
            PostingBatch.objects.select_for_update().filter(
                operation_type='order_payment.collect',
                source_content_type=content_type,
                source_object_id=str(source.pk),
                status=PostingBatch.Status.POSTED,
                reversal_of__isnull=True,
            )
        )
        if len(originals) != 1:
            raise InvalidTransition('لا يوجد قيد تحصيل أصلي وحيد ومرحّل لهذه الدفعة.')
        original_batch = originals[0]
        if original_batch.reversals.exists():
            raise InvalidTransition('تم عكس قيد هذه الدفعة مسبقاً.')

        original_entries = list(
            original_batch.entries.select_related('account').order_by('pk')
        )
        if len(original_entries) != 2 or not original_batch.is_balanced():
            raise InvalidTransition('قيد التحصيل الأصلي غير قابل للمطابقة المالية.')
        debits = [entry for entry in original_entries if entry.debit is not None]
        credits = [entry for entry in original_entries if entry.credit is not None]
        if len(debits) != 1 or len(credits) != 1:
            raise InvalidTransition('تكوين القيد الأصلي غير متوقع.')
        debit, credit = debits[0], credits[0]
        if debit.debit != source.amount_syp or credit.credit != source.amount_syp:
            raise InvalidTransition('مبلغ قيد التحصيل لا يطابق الدفعة.')
        if credit.account.account_type != FinancialAccount.AccountType.REVENUE:
            raise InvalidTransition('حساب الإيراد الأصلي غير صالح.')
        if debit.account.currency != 'SYP' or credit.account.currency != 'SYP':
            raise InvalidTransition('عملة الحسابات الأصلية لا تطابق عملة الدفعة.')
        if source.method == Payment.Method.CASH:
            if debit.account.scope != 'cashbox':
                raise InvalidTransition('حساب النقد الأصلي غير صالح.')
        elif debit.account.account_type != FinancialAccount.AccountType.CLEARING:
            raise InvalidTransition('حساب المقاصة الأصلي غير صالح.')
        for account in (debit.account, credit.account):
            require_active(account)
            ensure_period_open(context.date_for(source.order), account)

        original_cash = None
        active_cash = source.cash_movements.select_for_update().filter(
            is_cancelled=False
        )
        if source.method == Payment.Method.CASH:
            matches = list(active_cash.filter(
                direction=CashMovement.Direction.IN,
                financial_account=debit.account,
                amount_syp=source.amount_syp,
                is_generated=True, related_order=source.order,
                movement_type=CashMovement.MovementType.OTHER,
            ))
            if len(matches) != 1 or active_cash.count() != 1:
                raise InvalidTransition('حركة الصندوق الأصلية غير قابلة للتحقق.')
            original_cash = matches[0]
        elif active_cash.exists():
            raise InvalidTransition('هناك حركة صندوق غير متوقعة لدفعة غير نقدية.')

        batch = PostingBatch.objects.create(
            operation_type='order_payment.reverse',
            source_content_type=content_type,
            source_object_id=str(source.pk),
            business_date=context.date_for(source.order),
            status=PostingBatch.Status.DRAFT,
            idempotency_key=f'{context.idempotency_key}:batch',
            actor=context.actor, approver=approver,
            reversal_of=None,
            reason=reason, channel=context.channel,
            metadata={**dict(context.request_metadata),
                      'original_posting_batch_id': str(original_batch.pk),
                      'payment_method': source.method,
                      'refund_reference': refund_reference},
        )
        PostingEntry.objects.bulk_create([
            PostingEntry(batch=batch, account=entry.account,
                         debit=entry.credit, credit=entry.debit,
                         entry_role=entry.entry_role,
                         description=f'عكس تحصيل {source.order.display_number}')
            for entry in original_entries
        ])
        # The database forbids reversal_of on DRAFT batches. Post the
        # balanced journal first, then link it within this same transaction.
        post_balanced_batch(batch)
        batch.reversal_of = original_batch
        batch.save(update_fields=['reversal_of', 'updated_at'])

        source.is_reversed = True
        source.is_active = False
        source.reversal_reason = reason_code
        source.reversed_by = context.actor
        source.reversed_at = timezone.now()
        source.full_clean()
        source.save(update_fields=[
            'is_reversed','is_active','reversal_reason',
            'reversed_by','reversed_at','updated_at',
        ])

        if original_cash:
            CashMovement.objects.create(
                business_date=context.date_for(source.order),
                financial_account=original_cash.financial_account,
                movement_type=CashMovement.MovementType.REFUND,
                direction=CashMovement.Direction.OUT,
                amount_syp=source.amount_syp,
                related_order=source.order, related_payment=source,
                title=f'عكس دفع {source.order.display_number}', notes=reason,
                created_by=context.actor, approved_by=approver, is_generated=True,
            )
        ActivityLog.objects.create(
            actor=context.actor, action='payment_reversed',
            details={
                'payment_id': source.pk, 'order_id': source.order_id,
                'posting_key': context.idempotency_key, 'reason': reason,
                'reason_code': reason_code,
                'original_batch_id': str(original_batch.pk),
                'reversal_batch_id': str(batch.pk),
                'approver_id': approver.pk,
                'refund_reference': refund_reference,
            },
        )
        return source
    return dispatch('order_payment.reverse', payment, context, handle)
