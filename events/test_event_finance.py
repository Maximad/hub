"""Event finance attribution: no new expense, payment or fictional profit."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Expense, ExpenseCategory, FinancialAccount, PostingBatch, PostingEntry,
)
from events.finance import allocate_existing_event_expense, event_known_finance_report
from events.models import Event, EventExpenseAllocation


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventFinanceAttributionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='cost-admin', phone='983321', password='test', role='admin',
        )
        self.cashier = User.objects.create_user(
            username='cost-cashier', phone='983322', password='test', role='cashier',
        )
        self.event = Event.objects.create(
            title_ar='تقرير مالي',
            starts_at=timezone.now()+timedelta(days=5),
            status=Event.Status.PUBLISHED,
        )
        self.other = Event.objects.create(
            title_ar='فعالية ثانية',
            starts_at=timezone.now()+timedelta(days=6),
        )
        self.category = ExpenseCategory.objects.create(
            name_ar='أجور الصوت', code='event-audio',
            category_type=ExpenseCategory.CategoryType.EVENT_COST,
        )
        self.expense = Expense.objects.create(
            category=self.category, title='تجهيزات الصوت',
            amount_syp=1000, status=Expense.Status.APPROVED,
            business_date=timezone.localdate(),
        )

    def test_expense_is_only_attributed_not_posted_or_paid(self):
        link, created = allocate_existing_event_expense(
            self.event.pk, self.expense.pk, 600, 'معدات الصوت', actor=self.admin,
        )
        self.assertTrue(created)
        self.assertEqual(link.amount_syp, 600)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount_syp, 1000)
        self.assertEqual(self.expense.status, Expense.Status.APPROVED)
        self.assertIsNone(self.expense.payment_batch_id)
        report = event_known_finance_report(self.event.pk)
        self.assertEqual(report['verified_receipts_syp'], 0)
        self.assertEqual(report['verified_paid_costs_syp'], 0)
        self.assertEqual(report['allocated_unverified_costs_syp'], 600)
        self.assertFalse(report['is_complete_profit_and_loss'])
        again, created_again = allocate_existing_event_expense(
            self.event.pk, self.expense.pk, 600, 'معدات الصوت', actor=self.admin,
        )
        self.assertFalse(created_again)
        self.assertEqual(again.pk, link.pk)

    def test_two_events_cannot_overallocate_same_expense(self):
        allocate_existing_event_expense(
            self.event.pk, self.expense.pk, 600, 'تجهيزات مشتركة', actor=self.admin,
        )
        with self.assertRaises(ValidationError):
            allocate_existing_event_expense(
                self.other.pk, self.expense.pk, 500, 'تجهيزات مشتركة', actor=self.admin,
            )
        self.assertEqual(EventExpenseAllocation.objects.count(), 1)

    def test_external_project_expense_refused(self):
        self.expense.paid_from = Expense.PaidFrom.EXTERNAL
        self.expense.save(update_fields=['paid_from'])
        with self.assertRaises(ValidationError):
            allocate_existing_event_expense(
                self.event.pk, self.expense.pk, 100, 'ليس مال هَب', actor=self.admin,
            )
        self.assertFalse(EventExpenseAllocation.objects.exists())

    def test_paid_cost_needs_balanced_posted_journal(self):
        cost = FinancialAccount.objects.create(
            code='event-cost-test', name_ar='تكاليف الفعاليات',
            account_type=FinancialAccount.AccountType.EXPENSE, is_active=True,
        )
        cash = FinancialAccount.objects.create(
            code='event-cash-test', name_ar='الصندوق',
            account_type=FinancialAccount.AccountType.ASSET,
            scope='cashbox', is_active=True,
        )
        batch = PostingBatch.objects.create(
            operation_type='expense.pay', business_date=timezone.localdate(),
            status=PostingBatch.Status.POSTED,
            posted_at=timezone.now(), idempotency_key='event-cost-test-1',
        )
        PostingEntry.objects.create(batch=batch, account=cost, debit=1000)
        PostingEntry.objects.create(batch=batch, account=cash, credit=1000)
        self.expense.status = Expense.Status.PAID
        self.expense.payment_method = Expense.PaymentMethod.CASH
        self.expense.paid_from = Expense.PaidFrom.CASHBOX
        self.expense.posting_state = Expense.PostingState.POSTED
        self.expense.payment_batch = batch
        self.expense.save()
        allocate_existing_event_expense(
            self.event.pk, self.expense.pk, 400, 'مصاريف موسيقى', actor=self.admin,
        )
        report = event_known_finance_report(self.event.pk)
        self.assertEqual(report['verified_paid_costs_syp'], 400)
        self.assertEqual(report['known_cash_difference_syp'], -400)
        self.assertFalse(report['is_complete_profit_and_loss'])

    def test_event_finances_hidden_from_nonadmins(self):
        url = reverse('staff_event_finance', args=[self.event.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'ليس هذا صافي ربح الفعالية')
