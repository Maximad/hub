import json
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from core.models import CurrencyEntrySnapshot


class Command(BaseCommand):
    help = 'Read-only currency reconciliation; it never changes records.'

    def add_arguments(self, parser):
        parser.add_argument('--date-from', type=date.fromisoformat)
        parser.add_argument('--date-to', type=date.fromisoformat)
        parser.add_argument('--record-type')
        parser.add_argument('--currency', choices=['SYP_NEW', 'USD'])
        parser.add_argument('--reason')

    def handle(self, *args, **options):
        qs = CurrencyEntrySnapshot.objects.select_related('exchange_rate_record', 'confirmed_by', 'source_content_type')
        if options['date_from']: qs = qs.filter(created_at__date__gte=options['date_from'])
        if options['date_to']: qs = qs.filter(created_at__date__lte=options['date_to'])
        if options['currency']: qs = qs.filter(transaction_currency=options['currency'])
        if options['record_type']: qs = qs.filter(source_content_type__model=options['record_type'].lower())
        flagged = 0
        for row in qs.iterator():
            reasons = list(row.risk_reason_codes)
            expected = (row.original_amount * row.exchange_rate_to_base).quantize(Decimal('0.01'))
            if row.transaction_currency == 'USD' and not row.exchange_rate_record_id: reasons.append('usd_missing_rate')
            if row.transaction_currency == 'USD' and row.rate_effective_date and row.rate_effective_date > row.created_at.date(): reasons.append('future_rate')
            if expected != row.base_amount_syp: reasons.append('incorrect_base_conversion')
            account = getattr(row.source, 'financial_account', None)
            if account and account.currency not in {row.settlement_currency, row.settlement_currency[:3]}: reasons.append('account_currency_mismatch')
            if options['reason'] and options['reason'] not in reasons: continue
            if not reasons: continue
            flagged += 1
            self.stdout.write(json.dumps({
                'record_type': row.source_content_type.model, 'id': row.source_object_id,
                'date': row.created_at.date().isoformat(), 'user': getattr(row.confirmed_by, 'username', None),
                'amount': str(row.original_amount), 'currency': row.transaction_currency,
                'rate': str(row.exchange_rate_to_base), 'base_amount_syp': str(row.base_amount_syp),
                'suggested_amount': str(row.suggested_amount) if row.suggested_amount is not None else None,
                'reasons': reasons,
            }, ensure_ascii=False))
        self.stderr.write(f'flagged={flagged} (read-only)')
