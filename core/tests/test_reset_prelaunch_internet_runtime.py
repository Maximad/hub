import hashlib
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from core.models import HubVisit, HubVisitBrowserCredential, InternetEntitlement
from internet.models import (
    GuestWifiCodeAttempt,
    GuestWifiDailyAllowance,
    InternalStaffInternetGrant,
    InternetOperationsState,
)


class ResetPrelaunchInternetRuntimeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='internet-reset-admin', phone='reset-internet', password='test', role=User.Role.ADMIN,
        )

    def _backup(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        artifacts = {
            'database.sql': b'non-empty database dump',
            'media.tar.gz': b'non-empty media archive',
            'counts.tsv': b'core_internetentitlement\t1\n',
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
        names = (*artifacts.keys(), 'manifest.txt')
        (root / 'SHA256SUMS').write_text(
            ''.join(
                f'{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                for name in names
            ),
            encoding='utf-8',
        )
        (root / 'SUCCESS').touch()
        return manifest

    def test_reset_deletes_guest_and_staff_internet_runtime_rows(self):
        visit = HubVisit.objects.create(created_by=self.user)
        credential = HubVisitBrowserCredential.objects.create(
            visit=visit, token_hash='b' * 64,
        )
        entitlement = InternetEntitlement.objects.create(
            visit=visit,
            access_mode='timed_session',
            activation_policy='manual',
        )
        staff_grant = InternalStaffInternetGrant.objects.create(
            entitlement=entitlement,
            user=self.user,
        )
        allowance = GuestWifiDailyAllowance.objects.create(
            credential=credential,
            business_date=timezone.localdate(),
            initial_minutes_granted=60,
        )
        operations_state = InternetOperationsState.objects.create()
        code_attempt = GuestWifiCodeAttempt.objects.create(
            fingerprint_hash='c' * 64,
            window_started_at=timezone.now(),
            attempt_count=2,
        )

        call_command(
            'reset_prelaunch_data',
            execute=True,
            confirmation='RESET EXPERIMENTAL DATA',
            production_approved=True,
            backup_manifest=self._backup(),
            stdout=StringIO(),
        )

        for model, pk in (
            (InternalStaffInternetGrant, staff_grant.pk),
            (GuestWifiDailyAllowance, allowance.pk),
            (InternetOperationsState, operations_state.pk),
            (GuestWifiCodeAttempt, code_attempt.pk),
            (InternetEntitlement, entitlement.pk),
            (HubVisitBrowserCredential, credential.pk),
            (HubVisit, visit.pk),
        ):
            self.assertFalse(model.objects.filter(pk=pk).exists(), model._meta.label)

        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
