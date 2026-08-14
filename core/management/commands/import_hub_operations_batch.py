from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.services.operations_import import (
    BlockingError, OperationsImportEngine, HEADERS, ORDER, SHEETS,
)


class Command(BaseCommand):
    help = 'Safely preview or apply a Hub operations XLSX workbook.'

    def add_arguments(self, parser):
        parser.add_argument('workbook')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--only', choices=ORDER, action='append')
        parser.add_argument('--actor')

    def handle(self, *args, **options):
        path = Path(options['workbook'])
        if not path.exists():
            raise CommandError(f'Workbook does not exist: {path}')
        selected = set(options['only'] or ORDER)
        actor = None
        if options['actor']:
            actor = get_user_model().objects.filter(username=options['actor'], is_active=True).first()
            if actor is None:
                raise CommandError(f'Active actor not found: {options["actor"]}')
        engine = OperationsImportEngine()
        if options['apply'] and engine.has_financial_rows(path, selected) and actor is None:
            raise CommandError('--actor <username> is required to apply order payments or purchase receiving.')
        try:
            plan = engine.preview(path, actor, selected)
        except CommandError:
            raise
        self.print_plan(plan)
        for warning in plan.warnings:
            self.stderr.write(f'WARNING: {warning}')
        if plan.errors:
            for error in plan.errors:
                self.stderr.write(error)
            raise CommandError(f'Blocking validation errors: {len(plan.errors)}')
        if not options['apply']:
            self.stdout.write('DRY RUN — no database changes made')
            return
        try:
            engine.apply(path, actor, selected)
        except Exception as exc:
            raise CommandError(f'Apply rolled back: {exc}') from None
        self.stdout.write('APPLIED — database changes committed')

    def print_plan(self, plan):
        from decimal import Decimal
        self.stdout.write('Import plan')
        for section in ORDER:
            if plan.counts[section]:
                self.stdout.write(f'{section}:\n  ' + '\n  '.join(f'{key}={value}' for key, value in sorted(plan.counts[section].items())))
        if plan.stock:
            self.stdout.write('stock_effect:')
            for code, quantity in sorted(plan.stock.items()):
                self.stdout.write(f'  {code} +{quantity.quantize(Decimal(".001"))} {plan.stock_units[code]}')
        if plan.payments:
            self.stdout.write('payments_effect:')
            for method, amount in sorted(plan.payments.items()):
                self.stdout.write(f'  {method} {amount}')
        self.stdout.write(f'warnings={len(plan.warnings)} errors={len(plan.errors)}')
