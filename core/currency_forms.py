"""Shared, server-side currency handling for manually entered money fields."""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.currency import (SUPPORTED_CURRENCIES, SYP_NEW, USD, applicable_rate,
                           convert_to_base, enforce_risk, evaluate_currency_risk,
                           record_snapshot)


@dataclass
class CurrencyEntry:
    original_amount: object
    currency: str
    base_amount: object
    rate_record: object
    risk: object
    acknowledged: bool
    approver: object = None


class CurrencyEntryFormService:
    """One authoritative adapter used by every HTML manual-money workflow.

    Field names are deliberately stable so the same template and POST contract can
    be reused.  Supplying a prefix permits pages with more than one manual amount.
    """
    def __init__(self, request, *, operation, business_date=None, prefix='currency', approver=None):
        self.request = request
        self.operation = operation
        self.business_date = business_date or timezone.localdate()
        self.prefix = prefix
        self.approver = approver

    def field(self, name):
        return f'{self.prefix}_{name}'

    def clean(self, fallback_amount=None, *, manually_entered=True):
        data = self.request.POST
        amount = data.get(self.field('amount'))
        if amount in (None, ''):
            amount = fallback_amount
        currency = data.get(self.field('currency'), SYP_NEW)
        if currency not in dict(SUPPORTED_CURRENCIES):
            raise ValidationError('اختر عملة مدعومة.')
        rate_record = None
        if currency == USD:
            allow_stale = data.get(self.field('allow_stale_rate')) == 'on'
            rate_record = applicable_rate(self.business_date, allow_stale=allow_stale,
                                          user=self.request.user)
        base, rate = convert_to_base(amount, currency,
                                     rate_record.rate_to_base if rate_record else None)
        risk = evaluate_currency_risk(amount=amount, currency=currency,
            operation=self.operation, exchange_rate=rate, converted_base_amount=base,
            manually_entered=manually_entered)
        acknowledged = data.get(self.field('acknowledged')) == 'on'
        enforce_risk(risk, acknowledged=acknowledged,
                     user=self.approver or self.request.user)
        return CurrencyEntry(amount, currency, base, rate_record, risk,
                             acknowledged, self.approver)

    def snapshot(self, source, entry, field_name):
        return record_snapshot(source, operation=self.operation, field_name=field_name,
            amount=entry.original_amount, currency=entry.currency,
            business_date=self.business_date, rate_record=entry.rate_record,
            user=self.request.user, acknowledged=entry.acknowledged,
            approved_by=entry.approver if entry.risk.level == 'manager_review_required' else None)

    @property
    def context(self):
        return {'prefix': self.prefix, 'currencies': SUPPORTED_CURRENCIES,
                'base_label': 'ل.س جديدة', 'warning': 5000,
                'acknowledgment': 10000, 'manager': 50000}
