import csv
import json
from datetime import date
from io import StringIO

from django.core.management.base import BaseCommand, CommandError

from core.models import FinancialAccount
from core.services.finance_reconciliation import FinanceReconciler


class Command(BaseCommand):
    help = 'Read-only finance integrity scan; backfill writes require explicit --apply-backfill.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Exit non-zero when findings exist.')
        parser.add_argument('--format', choices=('json', 'csv', 'text'), default='text')
        parser.add_argument('--date-from', type=date.fromisoformat, dest='start')
        parser.add_argument('--date-to', type=date.fromisoformat, dest='end')
        parser.add_argument('--account', help='Financial account code or numeric primary key.')
        parser.add_argument('--apply-backfill', action='store_true', help='Explicitly apply safe, resumable projection backfills.')

    def handle(self, *args, **options):
        if options['check'] and options['apply_backfill']:
            raise CommandError('--check and --apply-backfill are separate modes.')
        if options['start'] and options['end'] and options['start'] > options['end']:
            raise CommandError('--date-from must not be after --date-to.')
        account = None
        if options['account']:
            lookup = {'pk': int(options['account'])} if options['account'].isdigit() else {'code': options['account']}
            try: account = FinancialAccount.objects.get(**lookup)
            except FinancialAccount.DoesNotExist: raise CommandError('Unknown financial account.')
        reconciler = FinanceReconciler(options['start'], options['end'], account)
        applied = reconciler.apply_backfill() if options['apply_backfill'] else 0
        findings = reconciler.run() if options['apply_backfill'] else reconciler.run()
        self._render(findings, options['format'], applied, options['apply_backfill'])
        if options['check'] and findings:
            raise CommandError(f'{len(findings)} finance reconciliation finding(s).')

    def _render(self, findings, output_format, applied, backfill):
        if output_format == 'json':
            self.stdout.write(json.dumps({'mode': 'apply-backfill' if backfill else 'read-only', 'applied': applied,
                'count': len(findings), 'findings': findings}, ensure_ascii=False, default=str, sort_keys=True))
        elif output_format == 'csv':
            stream = StringIO(); writer = csv.writer(stream)
            writer.writerow(['code', 'severity', 'model', 'record_id', 'message', 'review_required', 'details'])
            for row in findings:
                writer.writerow([row['code'], row['severity'], row['model'], row['record_id'], row['message'],
                    str(row['review_required']).lower(), json.dumps(row['details'], ensure_ascii=False, default=str, sort_keys=True)])
            self.stdout.write(stream.getvalue(), ending='')
        else:
            self.stdout.write(f"Finance reconciliation ({'apply-backfill' if backfill else 'read-only'}): {len(findings)} finding(s), {applied} change(s).")
            for row in findings:
                review = ' [REVIEW]' if row['review_required'] else ''
                self.stdout.write(f"{row['severity'].upper()} {row['code']} {row['model']}#{row['record_id']}{review}: {row['message']}")
