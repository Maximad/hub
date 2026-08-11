"""The single authoritative account-period close service."""
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import AuditEvent, DailyClose, DailyCloseRevision, FinancialAccount, PostingEntry
from .exceptions import InvalidTransition
from .policy import require_finance_actor


def _amount(value):
    return value or Decimal('0')


def close_totals(account, business_date):
    """Return cash changes from posted ledger entries only.

    Debit entries increase the cash account and credit entries decrease it. Reversal
    batches therefore naturally appear in the refunds/reversals bucket.
    """
    entries = PostingEntry.objects.filter(
        account=account, batch__business_date=business_date,
        batch__status__in=['posted', 'reversed'],
    )
    def signed(queryset):
        values = queryset.aggregate(debits=Sum('debit'), credits=Sum('credit'))
        return _amount(values['debits']) - _amount(values['credits'])

    transfers_in = signed(entries.filter(batch__operation_type__icontains='transfer', debit__isnull=False))
    transfers_out = -signed(entries.filter(batch__operation_type__icontains='transfer', credit__isnull=False))
    reversals = -signed(entries.filter(Q(batch__reversal_of__isnull=False) | Q(batch__operation_type__icontains='reverse')))
    ordinary = entries.exclude(batch__operation_type__icontains='transfer').exclude(
        Q(batch__reversal_of__isnull=False) | Q(batch__operation_type__icontains='reverse'))
    receipts = signed(ordinary.filter(debit__isnull=False))
    payments = -signed(ordinary.filter(credit__isnull=False))
    return {'cash_receipts': receipts, 'transfers_in': transfers_in, 'cash_payments': payments,
            'transfers_out': transfers_out, 'refunds_or_reversals': reversals}


def snapshot_for(close, totals=None):
    totals = totals or close_totals(close.account, close.business_date)
    return {
        'account_id': close.account_id, 'business_date': close.business_date.isoformat(),
        'opening_amount': str(close.opening_cash_syp), **{key: str(value) for key, value in totals.items()},
        'expected_amount': str(close.expected_cash_syp), 'counted_amount': str(close.actual_cash_counted_syp),
        'difference': str(close.cash_difference_syp), 'closer_id': close.closed_by_id,
        'approver_id': close.approved_by_id, 'closed_at': close.closed_at.isoformat() if close.closed_at else None,
    }


@transaction.atomic
def close(daily_close, context, actual_cash_counted_syp, notes='', opening_cash_syp=None):
    require_finance_actor(context, 'الإغلاق اليومي')
    if actual_cash_counted_syp is None:
        raise InvalidTransition('العد الفعلي للنقد مطلوب.')
    source = DailyClose.objects.select_for_update().select_related('account').get(pk=daily_close.pk)
    if not source.account_id:
        raise InvalidTransition('يجب تحديد الحساب المالي للإغلاق.')
    FinancialAccount.objects.select_for_update().get(pk=source.account_id)
    if source.status == DailyClose.Status.CLOSED and source.closed_at:
        return source
    totals = close_totals(source.account, source.business_date)
    opening = int(source.opening_cash_syp if opening_cash_syp is None else opening_cash_syp)
    expected = Decimal(opening) + totals['cash_receipts'] + totals['transfers_in'] - totals['cash_payments'] - totals['transfers_out'] - totals['refunds_or_reversals']
    counted = int(actual_cash_counted_syp)
    if source.pk:
        DailyCloseRevision.objects.create(daily_close=source, revision_type='before_close', snapshot=source.close_snapshot, created_by=context.actor)
    source.opening_cash_syp = opening
    source.expected_cash_syp = int(expected)
    source.actual_cash_counted_syp = counted
    source.cash_difference_syp = counted - int(expected)
    source.refunds_or_reversals_syp = int(totals['refunds_or_reversals'])
    source.notes = notes
    source.status = DailyClose.Status.CLOSED
    source.is_finalized = True
    source.closed_by = context.actor
    source.approved_by = context.approver
    source.closed_at = timezone.now()
    source.close_snapshot = snapshot_for(source, totals)
    source.full_clean(); source.save()
    DailyCloseRevision.objects.create(daily_close=source, revision_type='closed', snapshot=source.close_snapshot, created_by=context.actor)
    AuditEvent.objects.create(actor=context.actor, approver=context.approver, action='account_period_closed',
        source_content_type=ContentType.objects.get_for_model(source), source_object_id=str(source.pk),
        after_snapshot=source.close_snapshot, request_key=context.idempotency_key, channel=context.channel)
    return source


@transaction.atomic
def reopen(daily_close, context, reason):
    reason = (reason or '').strip()
    if not reason:
        raise InvalidTransition('سبب إعادة الفتح مطلوب.')
    actor = context.actor
    if not actor or not (actor.is_superuser or getattr(actor, 'role', '') == 'admin'):
        raise InvalidTransition('إعادة فتح فترة تحتاج صلاحية الإدارة المالية.')
    source = DailyClose.objects.select_for_update().get(pk=daily_close.pk)
    FinancialAccount.objects.select_for_update().filter(pk=source.account_id).first()
    before = source.close_snapshot
    DailyCloseRevision.objects.create(daily_close=source, revision_type='before_reopen', snapshot=before, reason=reason, created_by=actor)
    source.status = DailyClose.Status.REOPENED; source.is_finalized = False
    source.reopened_by = actor; source.reopened_at = timezone.now(); source.reopen_reason = reason
    source.save(update_fields=['status', 'is_finalized', 'reopened_by', 'reopened_at', 'reopen_reason', 'updated_at'])
    AuditEvent.objects.create(actor=actor, approver=context.approver, action='account_period_reopened',
        source_content_type=ContentType.objects.get_for_model(source), source_object_id=str(source.pk),
        before_snapshot=before, after_snapshot={'state': source.status, 'reason': reason},
        request_key=context.idempotency_key, channel=context.channel)
    return source


def enforce_open(business_date, account=None):
    from .engine import ensure_period_open
    return ensure_period_open(business_date, account)
