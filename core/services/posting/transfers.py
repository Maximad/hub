from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounts.permissions import can_approve_partial_payment
from core.models import CashMovement, PostingBatch, PostingEntry, Transfer
from .engine import dispatch
from .exceptions import InvalidTransition
from .ledger import post_balanced_batch


def approval_limit():
    return Decimal(str(getattr(settings, 'TRANSFER_APPROVAL_LIMIT_SYP', 1000000)))


def _requires_approval(amount):
    return amount >= approval_limit()


def _validate(source, context):
    if not context.actor:
        raise InvalidTransition('منفذ التحويل مطلوب.')
    if source.actor_id and source.actor_id != context.actor.pk:
        raise InvalidTransition('منفذ التحويل لا يطابق المستخدم الحالي.')
    if source.source_account_id == source.destination_account_id:
        raise InvalidTransition('يجب أن يختلف حساب المصدر عن حساب الوجهة.')
    if not source.source_account.is_active or not source.destination_account.is_active:
        raise InvalidTransition('يجب أن يكون حسابا المصدر والوجهة فعالين.')
    if source.source_account.currency != source.destination_account.currency:
        raise InvalidTransition('لا يمكن تحويل الرقم نفسه بين حسابين بعملتين مختلفتين. استخدم سير عمل تحويل عملة مصرحاً يحفظ المبلغين وسعر الصرف.')
    if source.amount <= 0:
        raise InvalidTransition('مبلغ التحويل يجب أن يكون موجباً.')
    if not source.business_date:
        raise InvalidTransition('تاريخ العمل مطلوب.')
    if not source.reason.strip():
        raise InvalidTransition('سبب التحويل مطلوب.')
    if _requires_approval(source.amount):
        if not context.approver:
            raise InvalidTransition(f'التحويلات بقيمة {approval_limit():,.0f} ل.س أو أكثر تحتاج موافقة.')
        if context.approver.pk == context.actor.pk:
            raise InvalidTransition('يجب أن يكون المعتمد شخصاً مختلفاً عن منفذ التحويل.')
        if not can_approve_partial_payment(context.approver):
            raise InvalidTransition('معتمد التحويل يجب أن يملك صلاحية المدير أو صاحب المحل.')


def _movement(source, context, *, account, direction, leg):
    outgoing = direction == CashMovement.Direction.OUT
    return CashMovement.objects.create(
        transfer=source, financial_account=account, transfer_leg=leg,
        business_date=source.business_date,
        movement_type=CashMovement.MovementType.CASH_WITHDRAWAL if outgoing else CashMovement.MovementType.CASH_DEPOSIT,
        direction=direction, amount_syp=source.amount, title=f'تحويل مالي {source.pk}', notes=source.reason,
        created_by=context.actor, approved_by=context.approver, is_generated=True,
    )


def post(transfer, context):
    """Post one transfer command, one balanced batch, and its two projections atomically."""
    def handle(source):
        if source.state != Transfer.State.DRAFT:
            raise InvalidTransition('يمكن ترحيل مسودة تحويل فقط.')
        source.actor = context.actor
        _validate(source, context)
        source.approver = context.approver if _requires_approval(source.amount) else context.approver
        source.full_clean(); source.save()
        batch = PostingBatch.objects.create(
            operation_type='transfer.post', source_content_type=ContentType.objects.get_for_model(source),
            source_object_id=str(source.pk), business_date=source.business_date,
            idempotency_key=f'{context.idempotency_key}:batch', actor=context.actor, approver=source.approver,
            reason=source.reason, channel=context.channel, metadata=dict(context.request_metadata),
        )
        PostingEntry.objects.bulk_create([
            PostingEntry(batch=batch, account=source.destination_account, debit=source.amount, description=source.reason),
            PostingEntry(batch=batch, account=source.source_account, credit=source.amount, description=source.reason),
        ])
        batch = post_balanced_batch(batch)
        _movement(source, context, account=source.source_account, direction=CashMovement.Direction.OUT, leg='outgoing')
        _movement(source, context, account=source.destination_account, direction=CashMovement.Direction.IN, leg='incoming')
        source.posting_batch=batch; source.state=Transfer.State.POSTED
        source.save(update_fields=['posting_batch', 'state', 'updated_at'])
        return source
    return dispatch('transfer.post', transfer, context, handle)


def reverse(transfer, context, reason):
    """Reverse both transfer sides exclusively through one balanced inverse batch."""
    def handle(source):
        if source.state != Transfer.State.POSTED or not reason.strip():
            raise InvalidTransition('يمكن عكس تحويل مرحل فقط مع ذكر السبب.')
        if not context.actor:
            raise InvalidTransition('منفذ العكس مطلوب.')
        original = PostingBatch.objects.select_for_update().get(pk=source.posting_batch_id)
        batch = PostingBatch.objects.create(
            operation_type='transfer.reverse', source_content_type=ContentType.objects.get_for_model(source),
            source_object_id=str(source.pk), business_date=context.date_for(source), reversal_of=original,
            idempotency_key=f'{context.idempotency_key}:batch', actor=context.actor, approver=context.approver,
            status=PostingBatch.Status.POSTED, posted_at=timezone.now(),
            reason=reason.strip(), channel=context.channel, metadata=dict(context.request_metadata),
        )
        PostingEntry.objects.bulk_create([
            PostingEntry(batch=batch, account=source.source_account, debit=source.amount, description=reason),
            PostingEntry(batch=batch, account=source.destination_account, credit=source.amount, description=reason),
        ])
        batch = post_balanced_batch(batch)
        reverse_context = context
        original_reason = source.reason
        source.reason = reason.strip()
        _movement(source, reverse_context, account=source.destination_account, direction=CashMovement.Direction.OUT, leg='reversal_outgoing')
        _movement(source, reverse_context, account=source.source_account, direction=CashMovement.Direction.IN, leg='reversal_incoming')
        source.reason = original_reason
        original.status=PostingBatch.Status.REVERSED; original.reversed_at=timezone.now()
        original.save(update_fields=['status', 'reversed_at', 'updated_at'])
        source.reversal_batch=batch; source.state=Transfer.State.REVERSED
        source.save(update_fields=['reversal_batch', 'state', 'updated_at'])
        return source
    return dispatch('transfer.reverse', transfer, context, handle)
