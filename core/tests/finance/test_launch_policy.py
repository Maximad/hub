from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from core.finance import finance_summary_for_date
from core.models import AuditEvent, DailyClose, DailyCloseRevision, FinancialAccount, Transfer
from core.services.posting import closing, transfers
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import ClosedPeriodError, InvalidTransition


class LaunchBootstrapTests(TestCase):
    def test_dry_run_writes_nothing_and_apply_is_idempotent(self):
        output = StringIO()
        call_command('bootstrap_launch_finance', dry_run=True, stdout=output)
        self.assertIn('DRY RUN', output.getvalue())
        self.assertEqual(FinancialAccount.objects.count(), 0)

        call_command('bootstrap_launch_finance', apply=True, stdout=StringIO())
        count = FinancialAccount.objects.count()
        call_command('bootstrap_launch_finance', apply=True, stdout=StringIO())
        self.assertEqual(FinancialAccount.objects.count(), count)
        self.assertTrue(FinancialAccount.objects.get(code='cash:main').is_active)
        self.assertFalse(FinancialAccount.objects.filter(code__in=[
            'inventory:purchases', 'payable:suppliers', 'clearing:owner_paid']).exists())


class LaunchPolicyAcceptanceTests(TestCase):
    day = date(2026, 8, 11)

    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_user(username='launch-admin', phone='+9631001', password='x', role='admin')
        self.other_admin = users.create_user(username='launch-approver', phone='+9631002', password='x', role='admin')
        self.cashier = users.create_user(username='launch-cashier', phone='+9631003', password='x', role='cashier')
        self.source = FinancialAccount.objects.create(code='cash:test-main', name_ar='رئيسي', account_type='asset', is_active=True)
        self.destination = FinancialAccount.objects.create(code='bank:test-main', name_ar='بنك', account_type='asset', is_active=True)

    def context(self, actor, key, approver=None, day=None):
        return PostingContext(actor=actor, approver=approver, business_date=day or self.day, idempotency_key=key, channel='acceptance')

    def transfer(self, amount):
        return Transfer(source_account=self.source, destination_account=self.destination,
                        amount=Decimal(amount), business_date=self.day, reason='سبب موثق')

    def test_role_and_transfer_threshold_policy(self):
        with self.assertRaisesRegex(InvalidTransition, 'الإدارة أو المالية'):
            transfers.post(self.transfer('49999'), self.context(self.cashier, 'role:rejected'))
        posted = transfers.post(self.transfer('49999'), self.context(self.admin, 'threshold:below'))
        self.assertEqual(posted.actor, self.admin)
        with self.assertRaisesRegex(InvalidTransition, 'تحتاج موافقة'):
            transfers.post(self.transfer('50000'), self.context(self.admin, 'threshold:missing'))
        with self.assertRaisesRegex(InvalidTransition, 'مختلفاً'):
            transfers.post(self.transfer('50000'), self.context(self.admin, 'threshold:same', self.admin))
        posted = transfers.post(self.transfer('50000'), self.context(self.admin, 'threshold:allowed', self.other_admin))
        self.assertEqual(posted.approver, self.other_admin)

    def test_inactive_account_and_closed_date_are_rejected(self):
        self.destination.is_active = False
        self.destination.save(update_fields=['is_active'])
        with self.assertRaisesRegex(InvalidTransition, 'فعالين'):
            transfers.post(self.transfer('1'), self.context(self.admin, 'inactive'))
        self.destination.is_active = True
        self.destination.save(update_fields=['is_active'])
        DailyClose.objects.create(account=self.source, business_date=self.day,
                                  status=DailyClose.Status.CLOSED, is_finalized=True)
        with self.assertRaises(ClosedPeriodError):
            transfers.post(self.transfer('1'), self.context(self.admin, 'closed'))

    def test_close_requires_finance_and_count_and_reopen_requires_admin_reason(self):
        close = DailyClose.objects.create(account=self.source, business_date=self.day,
                                          status=DailyClose.Status.OPEN, is_finalized=False)
        with self.assertRaisesRegex(InvalidTransition, 'الإدارة أو المالية'):
            closing.close(close, self.context(self.cashier, 'close:role'), 0)
        with self.assertRaisesRegex(InvalidTransition, 'العد الفعلي'):
            closing.close(close, self.context(self.admin, 'close:count'), None)
        closed = closing.close(close, self.context(self.admin, 'close:ok'), 0)
        with self.assertRaisesRegex(InvalidTransition, 'سبب'):
            closing.reopen(closed, self.context(self.admin, 'reopen:reason'), '')
        reopened = closing.reopen(closed, self.context(self.admin, 'reopen:ok'), 'تصحيح موثق')
        self.assertEqual(reopened.status, DailyClose.Status.REOPENED)

    def test_close_snapshot_report_refresh_reopen_and_posting(self):
        posted = transfers.post(self.transfer('125.50'), self.context(self.admin, 'close-flow:transfer'))
        transfers.reverse(posted, self.context(self.admin, 'close-flow:reverse'), 'تصحيح التحويل')
        close = DailyClose.objects.create(
            account=self.source, business_date=self.day,
            status=DailyClose.Status.OPEN, is_finalized=False,
        )
        closed = closing.close(
            close, self.context(self.admin, 'close-flow:close'),
            actual_cash_counted_syp=1000, opening_cash_syp=1000,
        )
        original_snapshot = dict(closed.close_snapshot)
        self.assertEqual(original_snapshot['opening_amount'], '1000')
        self.assertEqual(original_snapshot['expected_amount'], '1000')
        self.assertEqual(original_snapshot['counted_amount'], '1000')
        self.assertEqual(original_snapshot['difference'], '0')
        first_expected = finance_summary_for_date(self.day)['expected_cash_syp']
        closed.refresh_from_db()
        refreshed_expected = finance_summary_for_date(self.day)['expected_cash_syp']
        self.assertEqual(first_expected, refreshed_expected)
        self.assertEqual(first_expected, Decimal('1000.00'))

        with self.assertRaises(ClosedPeriodError):
            transfers.post(self.transfer('1'), self.context(self.admin, 'close-flow:blocked'))
        reopened = closing.reopen(
            closed, self.context(self.admin, 'close-flow:reopen'), 'إضافة عملية منسية',
        )
        revision = DailyCloseRevision.objects.get(daily_close=closed, revision_type='before_reopen')
        self.assertEqual(revision.snapshot, original_snapshot)
        revision.reason = 'محاولة تعديل'
        with self.assertRaises(ValidationError):
            revision.save()
        self.assertTrue(AuditEvent.objects.filter(
            action='account_period_reopened', source_object_id=str(closed.pk),
        ).exists())
        after_reopen = transfers.post(self.transfer('1'), self.context(self.admin, 'close-flow:after-reopen'))
        self.assertEqual(after_reopen.state, Transfer.State.POSTED)
        with self.assertRaisesRegex(InvalidTransition, 'إغلاق نهائي'):
            closing.reopen(reopened, self.context(self.admin, 'close-flow:reopen-twice'), 'مرة ثانية')
