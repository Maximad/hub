import csv
import json
from datetime import date
from io import StringIO

from django.core.management.base import BaseCommand, CommandError

from core.models import FinancialAccount
from core.services.finance_reconciliation import FinanceReconciler


class Command(BaseCommand):
    help = 'Read-only finance integrity and legacy-parity scan.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Exit non-zero when findings exist.')
        parser.add_argument('--format', choices=('json', 'csv', 'text'), default='text')
        parser.add_argument('--date-from', type=date.fromisoformat, dest='start')
        parser.add_argument('--date-to', type=date.fromisoformat, dest='end')
        parser.add_argument('--account', help='Financial account code or numeric primary key.')
        parser.add_argument('--scope', choices=FinanceReconciler.SCOPES, default='all',
                            help='Limit the scan; use expenses for the first rollout.')

    def handle(self, *args, **options):
        if options['start'] and options['end'] and options['start'] > options['end']:
            raise CommandError('--date-from must not be after --date-to.')
        account = None
        if options['account']:
            lookup = {'pk': int(options['account'])} if options['account'].isdigit() else {'code': options['account']}
            try: account = FinancialAccount.objects.get(**lookup)
            except FinancialAccount.DoesNotExist: raise CommandError('Unknown financial account.')
        reconciler = FinanceReconciler(options['start'], options['end'], account, options['scope'])
        findings = reconciler.run()
        self._render(findings, options['format'], options['scope'])
        if options['check'] and findings:
            raise CommandError(f'{len(findings)} finance reconciliation finding(s).')

    def _render(self, findings, output_format, scope):
        if output_format == 'json':
            self.stdout.write(json.dumps({'mode': 'read-only', 'scope': scope,
                'count': len(findings), 'findings': findings}, ensure_ascii=False, default=str, sort_keys=True))
        elif output_format == 'csv':
            stream = StringIO(); writer = csv.writer(stream)
            writer.writerow(['code', 'severity', 'model', 'record_id', 'message', 'review_required', 'details'])
            for row in findings:
                writer.writerow([row['code'], row['severity'], row['model'], row['record_id'], row['message'],
                    str(row['review_required']).lower(), json.dumps(row['details'], ensure_ascii=False, default=str, sort_keys=True)])
            self.stdout.write(stream.getvalue(), ending='')
        else:
            self.stdout.write(f"Finance reconciliation (read-only, scope={scope}): {len(findings)} finding(s).")
            for row in findings:
                review = ' [REVIEW]' if row['review_required'] else ''
                self.stdout.write(f"{row['severity'].upper()} {row['code']} {row['model']}#{row['record_id']}{review}: {row['message']}")
