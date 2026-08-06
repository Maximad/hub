from core.finance import build_close_values, close_snapshot
from core.models import ActivityLog, DailyClose, DailyCloseRevision
from django.utils import timezone
from .engine import dispatch
from .exceptions import InvalidTransition


def close(daily_close, context, actual_cash_counted_syp, notes='', opening_cash_syp=None):
    def handle(source):
        if source.status == DailyClose.Status.CLOSED and source.closed_at: return source
        DailyCloseRevision.objects.create(daily_close=source, revision_type='before_reclose', snapshot=close_snapshot(source), created_by=context.actor)
        for key,value in build_close_values(source.business_date,actual_cash_counted_syp,notes,opening_cash_syp).items(): setattr(source,key,value)
        source.status=DailyClose.Status.CLOSED; source.is_finalized=True; source.closed_by=context.actor; source.closed_at=timezone.now(); source.full_clean(); source.save()
        DailyCloseRevision.objects.create(daily_close=source,revision_type='closed',snapshot=close_snapshot(source),created_by=context.actor)
        ActivityLog.objects.create(actor=context.actor,action='daily_close_closed',details={'daily_close_id':source.pk,'posting_key':context.idempotency_key}); return source
    return dispatch('period.close',daily_close,context,handle,allow_closed=True)


def reopen(daily_close, context, reason):
    if not reason.strip(): raise InvalidTransition('سبب إعادة الفتح مطلوب.')
    def handle(source):
        DailyCloseRevision.objects.create(daily_close=source,revision_type='before_reopen',snapshot=close_snapshot(source),reason=reason,created_by=context.actor)
        source.status=DailyClose.Status.REOPENED; source.reopened_by=context.actor; source.reopened_at=timezone.now(); source.reopen_reason=reason; source.save()
        ActivityLog.objects.create(actor=context.actor,action='daily_close_reopened',details={'daily_close_id':source.pk,'posting_key':context.idempotency_key}); return source
    return dispatch('period.reopen',daily_close,context,handle,allow_closed=True)


def enforce_open(business_date):
    from .engine import ensure_period_open
    return ensure_period_open(business_date)
