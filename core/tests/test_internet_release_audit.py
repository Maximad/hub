import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import InternetBandwidthProfile, InternetEntitlement
from core.services.internet_internal_access import grant_internal_access


@override_settings(MIKROTIK_ENABLED=False)
class InternetReleaseAuditTests(TestCase):
    def _audit(self):
        output = StringIO()
        call_command('internet_release_audit', '--json', stdout=output)
        return json.loads(output.getvalue())

    def test_empty_database_has_privacy_safe_pass(self):
        data = self._audit()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['summary']['staff_grants_total'], 0)
        self.assertFalse(data['summary']['quota_enforcement_from_observations'])
        self.assertNotIn('username', str(data))
        self.assertNotIn('password', str(data))

    def test_inactive_staff_grants_are_warnings_not_silently_deleted(self):
        User = get_user_model()
        person = User.objects.create_user(
            username='release-staff-private', phone='+963900555501',
            password='not-for-output', role=User.Role.WAITER,
        )
        profile = InternetBandwidthProfile.objects.create(
            code='fast', name='Fast', router_profile_name='hub-full', is_active=True,
        )
        entitlement = grant_internal_access(
            staff_user=person, grant_kind='team', bandwidth_profile=profile,
            access_mode='unlimited', validity_value=30, validity_unit='days',
        )
        User.objects.filter(pk=person.pk).update(is_active=False)
        data = self._audit()
        self.assertEqual(data['status'], 'WARN')
        self.assertEqual(data['summary']['staff_grants_inactive_staff'], 1)
        self.assertNotIn('release-staff-private', json.dumps(data))
        self.assertNotIn('+963900555501', json.dumps(data))
        self.assertNotIn('not-for-output', json.dumps(data))
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, InternetEntitlement.Status.ACTIVE)

    def test_router_tls_disabled_is_hard_failure_without_disabling_it(self):
        with TemporaryDirectory() as root:
            ca_file = Path(root) / 'router-ca.crt'
            ca_file.write_text('test-only')
            with override_settings(
                MIKROTIK_ENABLED=True, MIKROTIK_VERIFY_TLS=False,
                MIKROTIK_CA_FILE=str(ca_file),
            ):
                with self.assertRaises(CommandError):
                    call_command('internet_release_audit', '--json', stdout=StringIO())

    def test_missing_ca_is_a_blocker_without_touching_router(self):
        with override_settings(
            MIKROTIK_ENABLED=True, MIKROTIK_VERIFY_TLS=True,
            MIKROTIK_CA_FILE='/not/a/real/ca.crt',
        ):
            with self.assertRaises(CommandError):
                call_command('internet_release_audit', '--json', stdout=StringIO())
