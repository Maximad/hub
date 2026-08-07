from django.apps import apps
from django.db import IntegrityError, transaction

from core.models import DailyClose, FinancialAccount, PostingCommand
from .exceptions import ClosedPeriodError, IdempotencyConflict


def ensure_period_open(business_date, account=None):
    """Lock the relevant account and its period close before allowing a posting."""
    account_id = getattr(account, 'pk', account)
    if account_id:
        FinancialAccount.objects.select_for_update().get(pk=account_id)
    closes = DailyClose.objects.select_for_update().filter(business_date=business_date, is_finalized=True)
    if account_id:
        closes = closes.filter(account_id=account_id)
    if closes.filter(status=DailyClose.Status.CLOSED).exists():
        raise ClosedPeriodError(f'تاريخ العمل {business_date} مغلق.')


def lock_instance(instance):
    return type(instance).objects.select_for_update().get(pk=instance.pk)


def lock_accounts(*querysets):
    """Evaluate relevant cash/inventory account querysets under row locks."""
    for queryset in querysets:
        list(queryset.select_for_update().order_by('pk'))


def _existing(receipt):
    if not receipt.result_type or not receipt.result_id:
        return None
    model = apps.get_model(receipt.result_type)
    return model.objects.get(pk=receipt.result_id)


def dispatch(command, source, context, handler, *, allow_closed=False):
    """Serialize a source and execute one command exactly once."""
    if not context.idempotency_key:
        raise IdempotencyConflict('مفتاح idempotency مطلوب.')
    with transaction.atomic():
        receipt = PostingCommand.objects.select_for_update().filter(key=context.idempotency_key).first()
        if receipt:
            if receipt.command != command:
                raise IdempotencyConflict('مفتاح idempotency مستخدم لأمر آخر.')
            return _existing(receipt)
        # UUID primary keys exist before the first save; they are not lockable rows yet.
        locked = lock_instance(source) if getattr(source, 'pk', None) and not source._state.adding else source
        if not allow_closed:
            accounts = [getattr(locked, name, None) for name in
                        ('financial_account', 'source_account', 'destination_account', 'account')]
            accounts = [account for account in accounts if account is not None]
            if accounts:
                for account in accounts:
                    ensure_period_open(context.date_for(locked), account)
            else:
                ensure_period_open(context.date_for(locked))
        try:
            # Keep the insert in a savepoint.  PostgreSQL marks a transaction as
            # broken after a uniqueness error until its savepoint is rolled back;
            # without this inner atomic block a concurrent duplicate could not
            # read and return the winning command below.
            with transaction.atomic():
                receipt = PostingCommand.objects.create(
                    key=context.idempotency_key, command=command,
                    source_type=locked._meta.label, source_id=str(locked.pk or ''),
                    actor=context.actor, channel=context.channel,
                    request_metadata=dict(context.request_metadata),
                )
        except IntegrityError:
            receipt = PostingCommand.objects.select_for_update().get(key=context.idempotency_key)
            if receipt.command != command:
                raise IdempotencyConflict('مفتاح idempotency مستخدم لأمر آخر.')
            return _existing(receipt)
        result = handler(locked)
        receipt.result_type = result._meta.label
        receipt.result_id = str(result.pk)
        receipt.save(update_fields=['result_type', 'result_id', 'updated_at'])
        return result
