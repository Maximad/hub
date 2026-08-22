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


def _snapshot_number(value):
    """Serialize numeric snapshot values canonically across database backends.

    PostgreSQL aggregate results retain the DecimalField scale (for example
    ``Decimal('125.00')``) while other backends/test paths may produce
    ``Decimal('125')``.  Persisting ``str(value)`` directly therefore makes the
    JSON snapshot depend on the database backend even though the monetary value
    is identical.  Normalize trailing zeroes while keeping fixed-point notation.
    """
    return format(Decimal(str(value or 0)).normalize(), 'f')


def close_totals(account, business_date):
    """Return cash changes from posted ledger entries only.

    Debit entries increase the cash account and credit entries decrease it. Reversal
    inflows and outflows stay separate so legacy refund fields remain non-negative.
    """
    entries = PostingEntry.objects.filter(
        account=account, batch__business_date=business_date,
        batch__status__in=['posted', 'reversed'],
    )
    def signed(queryset):
        values = queryset.aggregate(debits=Sum('debit'), credits=Sum('credit'))
        return _amount(values['debits']) - _amount(values['credits'])

    reversal_filter = Q(batch__reversal_of__isnull=False) | Q(batch__operation_type__icontains='reverse')
    # A transfer reversal is a reversal, not a second transfer. Keeping these
    # sets disjoint prevents it from changing expected cash twice.
    transfer_entries = entries.filter(batch__operation_type__icontains='transfer').exclude(reversal_filter)
    transfers_in = signed(transfer_entries.filter(debit__isnull=False))
    transfers_out = -signed(transfer_entries.filter(credit__isnull=False))
    reversal_entries = entries.filter(reversal_filter)
    reversal_inflows = signed(reversal_entries.filter(debit__isnull=False))
    reversal_outflows = -signed(reversal_entries.filter(credit__isnull=False))
    ordinary = entries.exclude(batch__operation_type__icontains='transfer').exclude(reversal_filter)
    receipts = signed(ordinary.filter(debit__isnull=False))
    payments = -signed(ordinary.filter(credit__isnull=False))
    return {'cash_receipts': receipts, 'transfers_in': transfers_in, 'cash_payments': payments,
            'transfers_out': transfers_out, 'reversal_inflows': reversal_inflows,
            'refunds_or_reversals': reversal_outflows}


def snapshot_for(close, totals=None):
    totals = totals or close_totals(close.account, close.business_date)
    return {
        'account_id': close.account_id, 'business_date': close.business_date.isoformat(),
        'opening_amount': _snapshot_number(close.opening_cash_syp),
        **{key: _snapshot_number(value) for key, value in totals.items()},
        'expected_amount': _snapshot_number(close.expected_cash_syp),
        'counted_amount': _snapshot_number(close.actual_cash_counted_syp),
        'difference': _snapshot_number(close.cash_difference_syp), 'closer_id': close.closed_by_id,
        'approver_id': close.approved_by_id, 'closed_at': close.closed_at.isoformat() if close.closed_at else None,
    }


@transaction.atomic
def close(daily_close, context, actual_cash_counted_syp, notes='', opening_cash_syp=None):
    require_finance_actor(context, 'الإغلاق اليومي')
    if actual_cash_counted_syp is None:
        raise InvalidTransition('العد الفعلي للنقد مطلوب.')
    account_id = DailyClose.objects.values_list('account_id', flat=True).get(pk=daily_close.pk)
    if not account_id:
        raise InvalidTransition('يجب تحديد الحساب المالي للإغلاق.')
    account = FinancialAccount.objects.select_for_update().get(pk=account_id)
    source = DailyClose.objects.select_for_update().get(pk=daily_close.pk)
    if source.account_id != account_id:
        raise InvalidTransition('تم تغيير الحساب المالي للإغلاق.')
    source.account = account
    if source.status == DailyClose.Status.CLOSED and source.closed_at:
        return source
    totals = close_totals(source.account, source.business_date)
    opening = int(source.opening_cash_syp if opening_cash_syp is None else opening_cash_syp)
    expected = (Decimal(opening) + totals['cash_receipts'] + totals['transfers_in']
                + totals['reversal_inflows'] - totals['cash_payments']
                - totals['transfers_out'] - totals['refunds_or_reversals'])
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
    if source.status != DailyClose.Status.CLOSED or not source.is_finalized:
        raise InvalidTransition('يمكن إعادة فتح إغلاق نهائي مغلق فقط.')
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
