from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.management.commands.launch_readiness import Command
from core.models import AuditEvent, DailyClose, FinancialAccount, Order
from core.services.finance_reconciliation import FinanceReconciler
from core.services.posting.closing import _snapshot_number
from reservations.models import Reservation


class FinanceAuditScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='audit-scope-admin', phone='+9631901', password='x', role='admin'
        )

    def test_non_finance_audit_event_is_not_a_finance_finding(self):
        reservation = Reservation.objects.create(name='Test', phone='1')
        AuditEvent.objects.create(
            actor=self.user,
            action='reservation_status_transition',
            source_content_type=ContentType.objects.get_for_model(Reservation),
            source_object_id=str(reservation.pk),
            channel='staff',
        )

        reconciler = FinanceReconciler()
        reconciler._audits()

        self.assertEqual(reconciler.findings, [])

    def test_core_operational_order_audit_is_not_a_finance_finding(self):
        order = Order.objects.create()
        AuditEvent.objects.create(
            actor=self.user,
            action='order_status_transition',
            source_content_type=ContentType.objects.get_for_model(Order),
            source_object_id=str(order.pk),
            before_snapshot={'status': Order.Status.NEW},
            after_snapshot={'status': Order.Status.ACCEPTED, 'reason': ''},
            channel='staff',
        )

        reconciler = FinanceReconciler()
        reconciler._audits()

        self.assertEqual(reconciler.findings, [])

    def test_finance_close_audit_still_requires_operation_provenance(self):
        account = FinancialAccount.objects.create(
            code='cash:audit-test', name_ar='اختبار', account_type='asset', is_active=True
        )
        close = DailyClose.objects.create(
            account=account,
            business_date=date(2026, 8, 22),
        )
        AuditEvent.objects.create(
            actor=self.user,
            action='account_period_closed',
            source_content_type=ContentType.objects.get_for_model(DailyClose),
            source_object_id=str(close.pk),
            channel='staff',
        )

        reconciler = FinanceReconciler()
        reconciler._audits()

        self.assertEqual([row['code'] for row in reconciler.findings], ['audit_missing_operation'])


class CloseSnapshotFormattingTests(TestCase):
    def test_snapshot_numbers_are_backend_independent(self):
        self.assertEqual(_snapshot_number(Decimal('125.00')), '125')
        self.assertEqual(_snapshot_number(Decimal('125.50')), '125.5')
        self.assertEqual(_snapshot_number(Decimal('0.00')), '0')
        self.assertEqual(_snapshot_number(125), '125')


class ReadinessIntegrityRolloutTests(TestCase):
    def _result(self, findings, *, ledger_writes):
        command = Command()
        command.results = []
        rollout = SimpleNamespace(
            ledger_writes=ledger_writes,
            dual_reads=False,
            report_reads=False,
        )
        with patch(
            'core.management.commands.launch_readiness.FinanceReconciler'
        ) as reconciler_cls, patch(
            'core.management.commands.launch_readiness.current_rollout',
            return_value=rollout,
        ):
            reconciler_cls.return_value.run.return_value = findings
            command._integrity()
        return command.results[-1]

    def test_legacy_payment_missing_posting_is_warn_before_ledger_writes(self):
        result = self._result(
            [{'code': 'payment_missing_posting'}],
            ledger_writes=False,
        )
        self.assertEqual(result['status'], 'WARN')
        self.assertIn('1', result['message'])

    def test_other_integrity_finding_still_fails_before_ledger_writes(self):
        result = self._result(
            [{'code': 'unbalanced_posting_batch'}],
            ledger_writes=False,
        )
        self.assertEqual(result['status'], 'FAIL')

    def test_legacy_payment_missing_posting_blocks_after_ledger_writes_enable(self):
        result = self._result(
            [{'code': 'payment_missing_posting'}],
            ledger_writes=True,
        )
        self.assertEqual(result['status'], 'FAIL')
