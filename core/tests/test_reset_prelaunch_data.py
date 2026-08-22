import hashlib
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from core.models import (
    ActivityLog, ExchangeRate, HubVisit, HubVisitBrowserCredential,
    InternetEntitlement, InternetNetworkOperation, InventoryItem,
    OperationsImportReceipt, Purchase, Room, SystemSetting,
)
from members.models import Program, ProgramEnrollment


class ResetPrelaunchDataCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='keeper', phone='100', password='test', role=User.Role.ADMIN,
        )
        self.room = Room.objects.create(name_ar='قاعة')
        self.item = InventoryItem.objects.create(name_ar='قهوة', current_quantity='12.500')
        self.setting = SystemSetting.objects.create()
        self.rate = ExchangeRate.objects.create(
            rate_to_base=100, effective_date=timezone.localdate(), created_by=self.user,
        )
        self.activity = ActivityLog.objects.create(actor=self.user, action='experiment')

    def _backup(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        artifacts = {
            'database.sql': b'non-empty database dump',
            'media.tar.gz': b'non-empty media archive',
            'counts.tsv': b'core_activitylog\t1\n',
        }
        for name, content in artifacts.items():
            (root / name).write_bytes(content)
        stamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        manifest = root / 'manifest.txt'
        manifest.write_text(
            '\n'.join((
                'format_version=1', f'created_utc={stamp}', 'git_commit=test',
                f'database_bytes={len(artifacts["database.sql"])}',
                f'media_archive_bytes={len(artifacts["media.tar.gz"])}',
                'database_tables=1', 'database_rows=1', 'media_files=0',
            )) + '\n', encoding='utf-8',
        )
        checksum_names = (*artifacts.keys(), 'manifest.txt')
        (root / 'SHA256SUMS').write_text(
            ''.join(f'{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                    for name in checksum_names), encoding='utf-8',
        )
        (root / 'SUCCESS').touch()
        return manifest

    def _execute(self, **kwargs):
        options = {
            'execute': True, 'confirmation': 'RESET EXPERIMENTAL DATA',
            'production_approved': True, 'backup_manifest': self._backup(),
        }
        options.update(kwargs)
        return call_command('reset_prelaunch_data', stdout=StringIO(), **options)

    def test_dry_run_reports_and_makes_no_writes(self):
        output = StringIO()
        call_command('reset_prelaunch_data', stdout=output)
        self.assertTrue(ActivityLog.objects.filter(pk=self.activity.pk).exists())
        self.assertIn('DRY RUN', output.getvalue())
        self.assertIn('PRESERVED', output.getvalue())

    def test_zero_inventory_dry_run_makes_no_writes(self):
        output = StringIO()
        call_command('reset_prelaunch_data', zero_inventory=True, stdout=output)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 12.5)
        self.assertTrue(ActivityLog.objects.filter(pk=self.activity.pk).exists())
        self.assertIn('quantities that WILL be reset to zero during execution: 1', output.getvalue())

    def test_wrong_confirmation_phrase_is_rejected(self):
        with self.assertRaisesMessage(CommandError, 'Execution requires'):
            call_command('reset_prelaunch_data', execute=True, confirmation='wrong', stdout=StringIO())
        self.assertTrue(ActivityLog.objects.filter(pk=self.activity.pk).exists())

    @override_settings(PRELAUNCH_BACKUP_ROOT='/definitely/missing')
    def test_missing_backup_is_rejected(self):
        with self.assertRaisesMessage(CommandError, 'No backup'):
            call_command(
                'reset_prelaunch_data', execute=True,
                confirmation='RESET EXPERIMENTAL DATA', production_approved=True,
                stdout=StringIO(),
            )
        self.assertTrue(ActivityLog.objects.filter(pk=self.activity.pk).exists())

    def test_error_rolls_back_every_delete(self):
        with mock.patch(
            'core.management.commands.reset_prelaunch_data.Command._run_checks',
            side_effect=RuntimeError('injected check failure'),
        ):
            with self.assertRaisesMessage(CommandError, 'rolled back'):
                self._execute(zero_inventory=True)
        self.assertTrue(ActivityLog.objects.filter(pk=self.activity.pk).exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 12.5)

    def test_execute_deletes_operations_and_preserves_allowlist(self):
        self._execute()
        self.assertFalse(ActivityLog.objects.exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 12.5)
        for model, pk in ((User, self.user.pk), (Room, self.room.pk),
                          (InventoryItem, self.item.pk), (SystemSetting, self.setting.pk),
                          (ExchangeRate, self.rate.pk)):
            self.assertTrue(model.objects.filter(pk=pk).exists(), model._meta.label)
        # Idempotence: the already-empty operational set succeeds again.
        self._execute()
        self.assertTrue(ExchangeRate.objects.filter(pk=self.rate.pk).exists())
        call_command('system_audit', stdout=StringIO())
        call_command('smoke_check', stdout=StringIO())

    def test_execute_zeroes_inventory_and_is_idempotent(self):
        self._execute(zero_inventory=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 0)
        self.assertFalse(ActivityLog.objects.exists())

        self._execute(zero_inventory=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 0)

    def test_execute_deletes_new_dependency_sensitive_operational_models(self):
        visit = HubVisit.objects.create(created_by=self.user)
        credential = HubVisitBrowserCredential.objects.create(
            visit=visit, token_hash='a' * 64,
        )
        entitlement = InternetEntitlement.objects.create(
            visit=visit,
            access_mode='timed_session',
            activation_policy='manual',
        )
        network_operation = InternetNetworkOperation.objects.create(
            entitlement=entitlement,
            operation=InternetNetworkOperation.Operation.PROVISION,
            idempotency_key='reset-test-network-operation',
        )
        purchase = Purchase.objects.create(business_date=timezone.localdate())
        import_receipt = OperationsImportReceipt.objects.create(
            import_key='reset-test-import', purchase=purchase,
        )
        program = Program.objects.create(code='launch-program', name_ar='برنامج')
        enrollment = ProgramEnrollment.objects.create(
            program=program, participant_name='مشارك تجريبي',
        )

        self._execute()

        for model, pk in (
            (HubVisitBrowserCredential, credential.pk),
            (HubVisit, visit.pk),
            (InternetNetworkOperation, network_operation.pk),
            (InternetEntitlement, entitlement.pk),
            (OperationsImportReceipt, import_receipt.pk),
            (Purchase, purchase.pk),
            (ProgramEnrollment, enrollment.pk),
        ):
            self.assertFalse(model.objects.filter(pk=pk).exists(), model._meta.label)
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())
        self.assertTrue(Program.objects.filter(pk=program.pk).exists())

    def test_empty_manifest_is_not_a_successful_backup(self):
        manifest = self._backup()
        manifest.write_bytes(b'')
        with self.assertRaisesMessage(CommandError, 'manifest is empty'):
            self._execute(backup_manifest=manifest)
