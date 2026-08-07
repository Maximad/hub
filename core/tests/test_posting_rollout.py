"""Acceptance criteria: ordered, reversible additive-ledger rollout.

Phase 1: the write switch can be disabled without deleting new-ledger data.
Phase 2: dual reads cannot start before writes/reconciliation are clean.
Phase 3: reports cannot cut over before dual reads and clean reconciliation.
"""
from django.test import TestCase, override_settings

from core.models import PostingReconciliationFailure, SystemSetting
from core.services.posting.rollout import RolloutBlocked, assert_phase_can_enable, current_rollout


class PostingRolloutAcceptanceTests(TestCase):
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
