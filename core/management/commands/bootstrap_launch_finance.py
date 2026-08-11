from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import FinancialAccount


ACCOUNTS = (
    ('cash:main', 'الصندوق الرئيسي', 'Main cash', 'asset', 'cashbox'),
    ('bank:main', 'الحساب البنكي الرئيسي', 'Main bank', 'asset', 'bank'),
    ('revenue:operating', 'الإيراد التشغيلي', 'Operating revenue', 'revenue', 'operating'),
    ('clearing:card', 'مقاصة البطاقات', 'Card tender clearing', 'clearing', 'tender'),
    ('clearing:bank', 'مقاصة التحويل البنكي', 'Bank tender clearing', 'clearing', 'tender'),
    ('clearing:mobile', 'مقاصة التحويل المحمول', 'Mobile tender clearing', 'clearing', 'tender'),
    ('clearing:external', 'المقاصة الخارجية', 'External tender clearing', 'clearing', 'tender'),
    ('expense:operating', 'المصروف التشغيلي', 'Operating expense', 'expense', 'operating'),
)


class Command(BaseCommand):
    help = 'Preview or idempotently prepare launch accounts approved by D01-D04 and D12-D14.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--dry-run', action='store_true')
        mode.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        changes = []
        for code, name_ar, name_en, account_type, scope in ACCOUNTS:
            account = FinancialAccount.objects.filter(code=code).first()
            if account and account.account_type != account_type:
                raise CommandError(f'{code}: existing account has type {account.account_type}, expected {account_type}')
            if not account:
                changes.append((code, 'create and activate'))
            elif not account.is_active or account.business_unit or account.scope != scope:
                changes.append((code, 'normalize global scope and activate'))
            else:
                changes.append((code, 'unchanged'))
        for code, action in changes:
            self.stdout.write(f'{code}: {action}')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN: no database writes performed.'))
            return
        with transaction.atomic():
            for code, name_ar, name_en, account_type, scope in ACCOUNTS:
                FinancialAccount.objects.update_or_create(code=code, defaults={
                    'name_ar': name_ar, 'name_en': name_en, 'account_type': account_type,
                    'scope': scope, 'business_unit': '', 'is_active': True,
                    'currency': 'SYP', 'negative_balance_policy': 'forbid',
                })
        self.stdout.write(self.style.SUCCESS('Launch finance accounts prepared.'))
