"""Acceptance criteria: ordered, reversible additive-ledger rollout.

Phase 1: the write switch can be disabled without deleting new-ledger data.
Phase 2: dual reads cannot start before writes/reconciliation are clean.
Phase 3: reports cannot cut over before dual reads and clean reconciliation.
"""
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from config.settings import optional_bool_env
from core.models import PostingReconciliationFailure, SystemSetting
from core.services.posting.rollout import RolloutBlocked, assert_phase_can_enable, current_rollout


class PostingRolloutAcceptanceTests(TestCase):
    def test_fresh_system_setting_defaults_all_phases_off(self):
        setting = SystemSetting()
        self.assertFalse(setting.posting_ledger_writes_enabled)
        self.assertFalse(setting.posting_dual_read_enabled)
        self.assertFalse(setting.posting_reports_enabled)

    def test_database_flags_apply_without_deployment_overrides(self):
        SystemSetting.objects.create(
            posting_ledger_writes_enabled=True,
            posting_dual_read_enabled=True,
            posting_reports_enabled=True,
        )
        with override_settings():
            for name in (
                'POSTING_LEDGER_WRITES_ENABLED',
                'POSTING_DUAL_READ_ENABLED',
                'POSTING_REPORT_READS_ENABLED',
            ):
                settings._wrapped.__dict__.pop(name, None)
            self.assertEqual(current_rollout(), current_rollout().__class__(True, True, True))

    def test_phase_1_kill_switch_returns_all_reads_to_legacy(self):
        SystemSetting.objects.create(posting_ledger_writes_enabled=False, posting_dual_read_enabled=True, posting_reports_enabled=True)
        policy = current_rollout()
        self.assertFalse(policy.ledger_writes)
        self.assertFalse(policy.dual_reads)
        self.assertFalse(policy.report_reads)

    @override_settings(POSTING_LEDGER_WRITES_ENABLED=False)
    def test_deployment_kill_switch_overrides_database(self):
        SystemSetting.objects.create(posting_ledger_writes_enabled=True, posting_dual_read_enabled=True, posting_reports_enabled=True)
        self.assertEqual(current_rollout(), current_rollout().__class__(False, False, False))

    @override_settings(POSTING_LEDGER_WRITES_ENABLED=True)
    def test_deployment_override_can_enable_writes(self):
        SystemSetting.objects.create(posting_ledger_writes_enabled=False)
        self.assertTrue(current_rollout().ledger_writes)

    @override_settings(
        POSTING_LEDGER_WRITES_ENABLED=True,
        POSTING_DUAL_READ_ENABLED=False,
        POSTING_REPORT_READS_ENABLED=True,
    )
    def test_explicit_read_overrides_obey_phase_ordering(self):
        SystemSetting.objects.create(
            posting_ledger_writes_enabled=False,
            posting_dual_read_enabled=True,
            posting_reports_enabled=True,
        )
        self.assertEqual(current_rollout(), current_rollout().__class__(True, False, False))

    def test_phase_2_requires_phase_1_and_clean_reconciliation(self):
        setting = SystemSetting.objects.create(posting_ledger_writes_enabled=False)
        with self.assertRaises(RolloutBlocked):
            assert_phase_can_enable('dual_reads')
        setting.posting_ledger_writes_enabled = True
        setting.save()
        PostingReconciliationFailure.objects.create(record_type='core.Payment', record_id='critical-1', reason='mismatch')
        with self.assertRaises(RolloutBlocked):
            assert_phase_can_enable('dual_reads')

    def test_phase_3_requires_dual_reads_and_clean_reconciliation(self):
        setting = SystemSetting.objects.create(posting_ledger_writes_enabled=True, posting_dual_read_enabled=False)
        with self.assertRaises(RolloutBlocked):
            assert_phase_can_enable('report_reads')
        setting.posting_dual_read_enabled = True
        setting.save()
        assert_phase_can_enable('report_reads')


class PostingRolloutEnvironmentParsingTests(TestCase):
    def test_common_boolean_forms(self):
        for value in ('True', '1', 'yes', 'ON'):
            with self.subTest(value=value), patch.dict('os.environ', {'FLAG': value}):
                self.assertIs(optional_bool_env('FLAG'), True)
        for value in ('False', '0', 'no', 'OFF'):
            with self.subTest(value=value), patch.dict('os.environ', {'FLAG': value}):
                self.assertIs(optional_bool_env('FLAG'), False)

    def test_unset_is_none_and_invalid_value_is_rejected(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(optional_bool_env('FLAG'))
        with patch.dict('os.environ', {'FLAG': 'maybe'}):
            with self.assertRaisesRegex(ValueError, 'FLAG must be one of'):
                optional_bool_env('FLAG')


class PostingRolloutMigrationTests(TransactionTestCase):
    migrate_from = ('core', '0030_alter_cashmovement_amount_syp')
    migrate_to = ('core', '0031_systemsetting_posting_rollout_flags')

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        # Migration tests mutate the shared schema. Capture every current app leaf
        # before migrating backwards so teardown restores the real project schema,
        # not a historical core-only milestone.
        self.latest_targets = self.executor.loader.graph.leaf_nodes()
        self.executor.migrate([self.migrate_from])

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.latest_targets)
        super().tearDown()

    def test_migration_adds_all_flags_off(self):
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        OldSystemSetting = old_apps.get_model('core', 'SystemSetting')
        setting_id = OldSystemSetting.objects.create().pk

        self.executor.loader.build_graph()
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps
        setting = new_apps.get_model('core', 'SystemSetting').objects.get(pk=setting_id)

        self.assertFalse(setting.posting_ledger_writes_enabled)
        self.assertFalse(setting.posting_dual_read_enabled)
        self.assertFalse(setting.posting_reports_enabled)
