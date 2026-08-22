from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import FinancialAccount


ACCOUNTS = (
    ('inventory:purchases', 'مخزون المشتريات', 'Purchase inventory', 'asset', 'inventory'),
    ('payable:suppliers', 'ذمم الموردين', 'Supplier payable', 'liability', 'payable'),
    ('payable:owner', 'مستحق للمالك', 'Payable to owner', 'liability', 'payable'),
    ('equity:owner_contribution', 'مساهمة المالك', 'Owner contribution', 'equity', 'equity'),
)


class Command(BaseCommand):
    help = 'Preview or apply the global purchase-finance accounts approved by D07, D08 and D11.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--dry-run', action='store_true')
        mode.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        rows = []
        for code, name_ar, name_en, account_type, scope in ACCOUNTS:
            account = FinancialAccount.objects.filter(code=code).first()
            if account and account.account_type != account_type:
                raise CommandError(f'{code}: existing account has type {account.account_type}, expected {account_type}')
            if account and (account.business_unit or '') != '':
                raise CommandError(f'{code}: existing account belongs to business_unit {account.business_unit!r}; expected global blank scope')
            action = 'create and activate' if not account else ('normalize safe attributes and activate' if not account.is_active or account.scope != scope else 'unchanged')
            rows.append((code, action))
        for code, action in rows: self.stdout.write(f'{code}: {action}')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN: no database writes performed.')); return
        with transaction.atomic():
            for code, name_ar, name_en, account_type, scope in ACCOUNTS:
                account, _ = FinancialAccount.objects.get_or_create(code=code, defaults={'account_type':account_type,'business_unit':''})
                account.name_ar=name_ar; account.name_en=name_en; account.scope=scope; account.is_active=True
                account.currency='SYP'; account.negative_balance_policy=FinancialAccount.NegativeBalancePolicy.FORBID
                account.save()
        self.stdout.write(self.style.SUCCESS('Purchase finance accounts prepared.'))
