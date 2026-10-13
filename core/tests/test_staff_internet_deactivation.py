"""Staff internal internet deactivation lifecycle and worker recovery."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import (
    ActivityLog, InternetBandwidthProfile, InternetEntitlement,
    InternetNetworkOperation, InternetRevenueShare, InternetSession, Order, Payment,
)
from core.services.internet_access import start_usage_session
from core.services.internet_internal_access import (
    grant_internal_access, usable_internal_grant_for_user,
)
from core.services.staff_internet_lifecycle import (
    disabled_staff_internet_grant_ids, revoke_disabled_staff_internet,
)


@override_settings(MIKROTIK_ENABLED=False)
class DeactivatedStaffInternetTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username='deactivation-target', phone='+963900444001',
            password='pass', role=User.Role.BARTENDER,
        )
        self.other = User.objects.create_user(
            username='active-colleague', phone='+963900444002',
            password='pass', role=User.Role.WAITER,
        )
        self.profile = InternetBandwidthProfile.objects.create(
            code='fast', name='Fast', router_profile_name='hub-full', is_active=True,
        )
        self.grant = self._grant(self.staff)
        self.other_grant = self._grant(self.other)

    def _grant(self, user):
        return grant_internal_access(
            staff_user=user, grant_kind='team',
            bandwidth_profile=self.profile, access_mode='unlimited',
            validity_value=30, validity_unit='days',
            max_concurrent_devices=1, max_registered_devices=1,
        )

    def test_normal_account_deactivation_revokes_after_commit_and_queues_disconnect(self):
        active = start_usage_session(self.grant)
        with self.captureOnCommitCallbacks(execute=True):
            self.staff.is_active = False
            self.staff.save(update_fields=['is_active'])
        self.grant.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(self.grant.status, InternetEntitlement.Status.CANCELLED)
        self.assertEqual(self.grant.cancellation_reason, 'staff_account_deactivated')
        self.assertEqual(active.status, InternetSession.Status.ENDED)
        self.assertEqual(active.lifecycle_end_reason, 'staff_account_deactivated')
        self.assertTrue(InternetNetworkOperation.objects.filter(
            entitlement=self.grant, operation=InternetNetworkOperation.Operation.DISCONNECT,
        ).exists())
        self.assertEqual(ActivityLog.objects.filter(
            action='internet.internal_staff_grant_revoked_on_deactivation',
            details__staff_user_id=self.staff.pk,
        ).count(), 1)
        self.other_grant.refresh_from_db()
        self.assertEqual(self.other_grant.status, InternetEntitlement.Status.ACTIVE)
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          InternetRevenueShare.objects.count()), (0, 0, 0))

    def test_database_bulk_deactivation_is_caught_by_worker_reconciler(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(disabled_staff_internet_grant_ids(limit=25), [self.grant.pk])
        stream = StringIO()
        call_command('reconcile_internet_lifecycle', limit=25,
                     dry_run=True, json=True, stdout=stream)
        self.assertIn('"inactive_staff_grants_to_revoke"', stream.getvalue())
        self.assertIn(str(self.grant.pk), stream.getvalue())
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.status, InternetEntitlement.Status.ACTIVE)
        self.assertFalse(InternetNetworkOperation.objects.filter(
            entitlement=self.grant, operation=InternetNetworkOperation.Operation.DISCONNECT,
        ).exists())

        call_command('reconcile_internet_lifecycle', limit=25, stdout=StringIO())
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.status, InternetEntitlement.Status.CANCELLED)
        self.assertTrue(InternetNetworkOperation.objects.filter(
            entitlement=self.grant, operation=InternetNetworkOperation.Operation.DISCONNECT,
        ).exists())

    def test_repeated_reconciliation_is_idempotent_and_does_not_touch_colleagues(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(revoke_disabled_staff_internet(limit=20), 1)
        self.assertEqual(revoke_disabled_staff_internet(limit=20), 0)
        self.assertEqual(InternetNetworkOperation.objects.filter(
            entitlement=self.grant, operation=InternetNetworkOperation.Operation.DISCONNECT,
        ).count(), 1)
        self.assertEqual(ActivityLog.objects.filter(
            action='internet.internal_staff_grant_revoked_on_deactivation',
        ).count(), 1)
        self.assertTrue(usable_internal_grant_for_user(self.other))
        self.assertIsNone(usable_internal_grant_for_user(self.staff))

    def test_reactivation_never_silently_restores_cancelled_grant(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(revoke_disabled_staff_internet(user_id=self.staff.pk), 1)
        self.staff.is_active = True
        self.staff.save(update_fields=['is_active'])
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.status, InternetEntitlement.Status.CANCELLED)
        self.assertIsNone(usable_internal_grant_for_user(self.staff))
        fresh = self._grant(self.staff)
        self.assertNotEqual(fresh.pk, self.grant.pk)
        self.assertIsNotNone(usable_internal_grant_for_user(self.staff))

    def test_dry_run_and_limited_reconciler_do_not_touch_active_user(self):
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(disabled_staff_internet_grant_ids(user_id=self.other.pk), [])
        self.assertEqual(revoke_disabled_staff_internet(user_id=self.other.pk), 0)
        self.other_grant.refresh_from_db()
        self.assertEqual(self.other_grant.status, InternetEntitlement.Status.ACTIVE)

    def test_deactivation_during_rolled_back_transaction_does_not_revoke(self):
        from django.db import transaction
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    self.staff.is_active = False
                    self.staff.save(update_fields=['is_active'])
                    raise RuntimeError('rollback the account edit')
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.status, InternetEntitlement.Status.ACTIVE)
