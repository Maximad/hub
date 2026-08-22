"""Safely remove experimental transactions while retaining Hub master data."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone


CONFIRMATION = 'RESET EXPERIMENTAL DATA'
MAX_BACKUP_AGE_HOURS = 24

# Children precede parents.  This is deliberately explicit: adding a model to an
# installed app can never silently make it eligible for this destructive command.
DELETE_MODELS = (
    'core.NotificationRecipient', 'core.NotificationLog', 'core.NotificationEvent',
    'core.InternetRevenueShareAdjustment', 'core.InternetRevenueShare',
    'core.InternetUsageLedger', 'core.InternetNetworkOperation',
    'core.InternetAccessDevice', 'core.InternetSession',
    'members.CommercialAllocation', 'core.InternetEntitlement',
    'members.ProgramEnrollment',
    'members.MemberCreditLedger', 'members.MemberActivationToken',
    'members.MemberDeviceToken', 'members.MembershipSubscription',
    'reservations.Reservation', 'vendors.VendorParticipation', 'events.EventMedia',
    'events.EventTicketType', 'events.Event',
    'core.HubVisitBrowserCredential', 'core.HubVisit',
    'core.CurrencyEntrySnapshot', 'core.AuditEvent', 'core.ActivityLog',
    'core.PostingReconciliationFailure', 'core.FinanceReviewItem',
    'core.FinanceReconciliationState', 'core.PostingEntry', 'core.PostingCommand',
    'core.PurchasePayment', 'core.PurchaseReturnLine', 'core.StockMovement',
    'core.PurchaseReturn', 'core.PurchaseReceiptLine', 'core.PurchaseReceipt',
    'core.PurchaseItem', 'core.OperationsImportReceipt', 'core.Purchase',
    'core.ProductionBatchIngredient', 'core.ProductionBatch',
    'core.CashMovement', 'core.Transfer', 'core.DailyCloseRevision',
    'core.DailyClose', 'core.Expense', 'core.OrderDiscount', 'core.Payment',
    'core.OrderItem', 'core.Order', 'core.PostingBatch', 'core.Shift',
)

PRESERVED_GROUPS = (
    'migrations, users, groups, roles and permissions',
    'members/customer profiles and notification preferences',
    'rooms, tables/areas and their public QR codes',
    'catalog/menu sections, categories, products, options, tags and recipes',
    'InventoryItem master records (current_quantity may optionally be reset to zero)',
    'expense categories, financial accounts and membership/internet configuration',
    'system/page settings, Wi-Fi configuration and ExchangeRate records',
    'vendors and every MediaAsset and media file',
)


class Command(BaseCommand):
    help = 'Dry-run or safely delete experimental pre-launch operational records.'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--confirmation', default='')
        parser.add_argument('--production-approved', action='store_true')
        parser.add_argument(
            '--zero-inventory', action='store_true',
            help='Preserve InventoryItem rows but establish a zero-quantity launch baseline.',
        )
        parser.add_argument(
            '--backup-manifest', type=Path,
            help='Path to manifest.txt in a recent backup (or its backup directory).',
        )

    def handle(self, *args, **options):
        model_list = [apps.get_model(label) for label in DELETE_MODELS]
        before = {model._meta.label: model.objects.count() for model in model_list}
        inventory_model = apps.get_model('core.InventoryItem')
        inventory_total = inventory_model.objects.count()
        inventory_nonzero = inventory_model.objects.exclude(current_quantity=0).count()
        self._report('BEFORE / DELETION PLAN (dependency order)', before)
        self.stdout.write('\nPRESERVED (never selected for deletion):')
        for item in PRESERVED_GROUPS:
            self.stdout.write(f'  - {item}')
        if options['zero_inventory']:
            self.stdout.write('\nInventory baseline requested:')
            self.stdout.write(f'  items preserved: {inventory_total}')
            self.stdout.write(f'  non-zero quantities: {inventory_nonzero}')
            self.stdout.write(
                f'  quantities that WILL be reset to zero during execution: {inventory_nonzero}'
            )

        if not options['execute']:
            self.stdout.write(self.style.WARNING('\nDRY RUN: no records were changed.'))
            return
        if options['confirmation'] != CONFIRMATION:
            raise CommandError(f'Execution requires --confirmation "{CONFIRMATION}".')
        if not settings.DEBUG:
            self.stdout.write(self.style.ERROR('\n!!! PRODUCTION-SAFETY WARNING: DEBUG=False !!!'))
            if not options['production_approved']:
                raise CommandError('Refusing execution without --production-approved when DEBUG=False.')

        manifest = self._validate_backup(options['backup_manifest'])
        self.stdout.write(self.style.SUCCESS(f'Validated recent backup: {manifest}'))
        preserved_before = self._preserved_counts(model_list)
        deleted = {}
        try:
            with transaction.atomic():
                # Nullable self-PROTECT links otherwise prevent deleting a full
                # experimental graph. They are severed inside the same rollback
                # boundary before their rows are removed.
                apps.get_model('core.PostingBatch').objects.update(reversal_of=None)
                apps.get_model('core.AuditEvent').objects.update(
                    reversal_of=None, correction_of=None,
                )
                for model in model_list:
                    count, _ = model.objects.all().delete()
                    deleted[model._meta.label] = count
                quantities_zeroed = 0
                if options['zero_inventory']:
                    quantities_zeroed = inventory_model.objects.exclude(
                        current_quantity=0,
                    ).update(current_quantity=0)
                self._reset_sequences(model_list)
                self._run_checks(model_list, preserved_before)
                if options['zero_inventory'] and inventory_model.objects.exclude(
                    current_quantity=0,
                ).exists():
                    raise RuntimeError('non-zero InventoryItem quantities remain')
        except Exception as exc:
            raise CommandError(f'Reset failed; the transaction was rolled back: {exc}') from exc

        after = {model._meta.label: model.objects.count() for model in model_list}
        self._report('AFTER', after)
        self.stdout.write('\nDeleted top-level/cascaded rows reported per step:')
        for label in DELETE_MODELS:
            self.stdout.write(f'  {label}: {deleted[label]}')
        if options['zero_inventory']:
            self.stdout.write('\nInventory baseline:')
            self.stdout.write(f'  items preserved: {inventory_model.objects.count()}')
            self.stdout.write(f'  quantities zeroed: {quantities_zeroed}')
            self.stdout.write(
                '  non-zero remaining: '
                f'{inventory_model.objects.exclude(current_quantity=0).count()}'
            )
        self.stdout.write(self.style.SUCCESS('\nReset complete; orphan and reconciliation checks passed.'))

    def _report(self, heading, counts):
        self.stdout.write(f'\n=== {heading} ===')
        for label in DELETE_MODELS:
            self.stdout.write(f'  {label}: {counts[label]}')

    def _validate_backup(self, requested):
        if requested:
            manifest = requested / 'manifest.txt' if requested.is_dir() else requested
        else:
            root = Path(getattr(settings, 'PRELAUNCH_BACKUP_ROOT', '/opt/hub/backups/production'))
            candidates = (
                sorted(path for path in root.glob('hub-*/manifest.txt')
                       if (path.parent / 'SUCCESS').is_file())
                if root.is_dir() else []
            )
            if not candidates:
                raise CommandError('No backup supplied or found; use --backup-manifest.')
            manifest = candidates[-1]
        directory = manifest.parent
        required = ('database.sql', 'media.tar.gz', 'counts.tsv', 'manifest.txt', 'SHA256SUMS', 'SUCCESS')
        if any(not (directory / name).is_file() for name in required):
            raise CommandError('Backup is incomplete (required artifacts or SUCCESS marker missing).')
        if manifest.stat().st_size == 0:
            raise CommandError('Backup manifest is empty.')
        values = {}
        try:
            for line in manifest.read_text(encoding='utf-8').splitlines():
                key, value = line.split('=', 1)
                values[key] = value
            created = datetime.strptime(values['created_utc'], '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime_timezone.utc)
            if values['format_version'] != '1':
                raise ValueError('unsupported format')
            if int(values['database_bytes']) <= 0 or int(values['media_archive_bytes']) <= 0:
                raise ValueError('empty artifact recorded')
            if int(values['database_tables']) <= 0 or not values['git_commit'].strip():
                raise ValueError('backup identity/counts missing')
        except (KeyError, ValueError, OSError) as exc:
            raise CommandError(f'Backup manifest is invalid: {exc}') from exc
        max_age = getattr(settings, 'PRELAUNCH_BACKUP_MAX_AGE_HOURS', MAX_BACKUP_AGE_HOURS)
        age = timezone.now() - created
        if age.total_seconds() < -300 or age.total_seconds() > max_age * 3600:
            raise CommandError(f'Backup is not recent (maximum age: {max_age} hours).')
        if (directory / 'database.sql').stat().st_size != int(values['database_bytes']):
            raise CommandError('Database artifact size does not match the manifest.')
        if (directory / 'media.tar.gz').stat().st_size != int(values['media_archive_bytes']):
            raise CommandError('Media artifact size does not match the manifest.')
        if (directory / 'counts.tsv').stat().st_size == 0:
            raise CommandError('Backup table counts are empty.')
        self._verify_checksums(directory)
        return manifest

    def _verify_checksums(self, directory):
        lines = (directory / 'SHA256SUMS').read_text(encoding='utf-8').splitlines()
        expected_names = {'database.sql', 'media.tar.gz', 'counts.tsv', 'manifest.txt'}
        seen = set()
        for line in lines:
            try:
                digest, name = line.split(maxsplit=1)
                name = name.lstrip('*')
            except ValueError as exc:
                raise CommandError('Malformed SHA256SUMS.') from exc
            if name not in expected_names or len(digest) != 64:
                raise CommandError('SHA256SUMS contains an unexpected entry.')
            actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
            if actual != digest:
                raise CommandError(f'Backup checksum failed for {name}.')
            seen.add(name)
        if seen != expected_names:
            raise CommandError('SHA256SUMS does not cover every required backup artifact.')

    def _preserved_counts(self, deleted_models):
        deleted = set(deleted_models)
        return {m._meta.label: m.objects.count() for m in apps.get_models()
                if m not in deleted and m._meta.managed and not m._meta.proxy}

    def _run_checks(self, deleted_models, preserved_before):
        # Database constraints cover concrete FKs.  Explicitly inspect GFKs because
        # the database cannot: every model containing one must itself be reset.
        deleted = set(deleted_models)
        for model in apps.get_models():
            has_gfk = any(field.__class__.__name__ == 'GenericForeignKey'
                          for field in model._meta.private_fields)
            if has_gfk and model not in deleted:
                raise RuntimeError(f'Uninspected retained GenericForeignKey: {model._meta.label}')
        after = self._preserved_counts(deleted_models)
        changed = {label: (count, after[label]) for label, count in preserved_before.items()
                   if after[label] != count}
        if changed:
            raise RuntimeError(f'preservation allowlist changed: {changed}')
        for model in deleted_models:
            if model.objects.exists():
                raise RuntimeError(f'orphan operational rows remain in {model._meta.label}')

    def _reset_sequences(self, model_list):
        sql = connection.ops.sequence_reset_sql(no_style(), model_list)
        if sql:
            with connection.cursor() as cursor:
                for statement in sql:
                    cursor.execute(statement)
