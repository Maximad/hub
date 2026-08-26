from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from catalog.models import MediaAsset
from core.models import CashMovement, Expense, ExpenseCategory, FinancialAccount, PostingCommand
from core.services.finance_reconciliation import FinanceReconciler
from vendors.models import Vendor


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class StaffExpenseFormStabilityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username='expense-admin', password='pass', phone='+9911', role='admin'
        )
        self.cashier = user_model.objects.create_user(
            username='expense-cashier', password='pass', phone='+9912', role='cashier'
        )
        self.waiter = user_model.objects.create_user(
            username='expense-waiter', password='pass', phone='+9913', role='waiter'
        )
        self.kitchen = user_model.objects.create_user(
            username='expense-kitchen', password='pass', phone='+9914', role='kitchen'
        )
        ExpenseCategory.objects.create(name_ar='', name_en='', code='legacy-expense')
        Vendor.objects.create(name_ar='', name_en='', phone='', contact_person='')
        FinancialAccount.objects.create(
            code='ACTIVE-LEGACY', name_ar='', name_en='', account_type='legacy-value',
            currency='OLD', is_active=True,
        )
        FinancialAccount.objects.create(
            code='INACTIVE', name_ar='Inactive', account_type=FinancialAccount.AccountType.ASSET,
            is_active=False,
        )
        self.missing_media = MediaAsset.objects.create(
            title_ar='', title_en='', media_type=MediaAsset.MediaType.DOCUMENT,
            file='uploads/file-that-does-not-exist.pdf',
        )

    def _get_as(self, user, language='ar', with_messages=False):
        self.client.force_login(user)
        if with_messages:
            session = self.client.session
            session['ordinary_staff_preference'] = {'compact': True}
            session['_messages'] = (
                '[["__json_message",0,25,"\\u0646\\u062c\\u0627\\u062d '
                '\\u0633\\u0627\\u0628\\u0642",""],'
                '["__json_message",0,40,"\\u062e\\u0637\\u0623 '
                '\\u0633\\u0627\\u0628\\u0642",""]]'
            )
            session.save()
        with translation.override(language):
            return self.client.get(
                reverse('staff_finance_expense_new'),
                HTTP_ACCEPT_LANGUAGE=f'{language},en;q=0.8',
                HTTP_USER_AGENT='Mozilla/5.0',
            )

    def test_finance_users_render_with_real_middleware_and_legacy_rows(self):
        for user in (self.admin, self.cashier):
            for language in ('ar', 'en'):
                with self.subTest(role=user.role, language=language):
                    response = self._get_as(user, language, with_messages=True)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'legacy-expense')
                    self.assertContains(response, 'ACTIVE-LEGACY')
                    self.assertNotContains(response, 'INACTIVE')

    def test_non_finance_users_redirect_without_server_error(self):
        for user in (self.waiter, self.kitchen):
            with self.subTest(role=user.role):
                response = self._get_as(user)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse('staff_home'))

    def test_expense_option_model_strings_always_return_text(self):
        category = ExpenseCategory(name_ar=None, name_en=None, code=None)
        vendor = Vendor(name_ar=None, name_en=None, phone=None, uuid=None)
        account = FinancialAccount(code=None, name_ar=None, name_en=None)
        media = MediaAsset(title_ar=None, title_en=None, file='', uuid=None)
        for value in (category, vendor, account, media):
            with self.subTest(model=type(value).__name__):
                self.assertIsInstance(str(value), str)
                self.assertTrue(str(value))

    def test_missing_media_file_is_not_opened_to_render_option(self):
        self.assertEqual(self.missing_media.safe_url, '')
        response = self._get_as(self.admin)
        self.assertEqual(response.status_code, 200)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class StaffExpensePostTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username='expense-post-admin', password='pass', phone='+9921', role='admin'
        )
        self.category = ExpenseCategory.objects.create(
            name_ar='تشغيل', name_en='Operations', code='expense-post', is_active=True
        )
        self.cash_account = FinancialAccount.objects.create(
            code='CASH-POST', name_ar='الصندوق', account_type=FinancialAccount.AccountType.ASSET,
            scope='cashbox', currency='SYP', is_active=True,
        )
        self.liability_account = FinancialAccount.objects.create(
            code='LIABILITY-POST', name_ar='ذمم دائنة',
            account_type=FinancialAccount.AccountType.LIABILITY, currency='SYP', is_active=True,
        )
        self.client.force_login(self.admin)
        self.url = reverse('staff_finance_expense_new')

    def _payload(self, status, **overrides):
        payload = {
            'business_date': '2026-08-09',
            'category': str(self.category.pk),
            'payee_type': Expense.PayeeType.MANUAL,
            'supplier_name': 'مورد الاختبار',
            'title': 'مصروف اختبار POST',
            'description': 'اختبار مسار الحفظ الحقيقي',
            'amount_syp': '12500',
            'currency_amount': '12500',
            'currency_currency': 'SYP_NEW',
            'currency_acknowledged': 'on',
            'payment_method': '',
            'paid_from': Expense.PaidFrom.UNPAID,
            'status': status,
            'financial_account': '',
            'liability_account': '',
        }
        payload.update(overrides)
        if 'amount_syp' in overrides and 'currency_amount' not in overrides:
            payload['currency_amount'] = overrides['amount_syp']
        return payload

    def test_draft_post_redirects_and_uses_deterministic_draft_key(self):
        response = self.client.post(
            self.url, self._payload(Expense.Status.DRAFT), HTTP_IDEMPOTENCY_KEY='expense-draft'
        )

        self.assertRedirects(
            response, reverse('staff_finance_expenses'), fetch_redirect_response=False
        )
        expense = Expense.objects.get()
        self.assertEqual(expense.status, Expense.Status.DRAFT)
        self.assertEqual(PostingCommand.objects.get().key, 'expense-draft:draft')

    def test_paid_post_retry_is_idempotent_for_expense_commands_and_cash_movement(self):
        payload = self._payload(
            Expense.Status.PAID,
            payment_method=Expense.PaymentMethod.CASH,
            paid_from=Expense.PaidFrom.CASHBOX,
            financial_account=str(self.cash_account.pk),
        )

        first = self.client.post(self.url, payload, HTTP_IDEMPOTENCY_KEY='expense-paid')
        second = self.client.post(self.url, payload, HTTP_IDEMPOTENCY_KEY='expense-paid')

        self.assertRedirects(
            first, reverse('staff_finance_expenses'), fetch_redirect_response=False
        )
        self.assertRedirects(
            second, reverse('staff_finance_expenses'), fetch_redirect_response=False
        )
        self.assertEqual(Expense.objects.count(), 1)
        expense = Expense.objects.get()
        self.assertEqual(expense.status, Expense.Status.PAID)
        self.assertEqual(expense.financial_account, self.cash_account)
        self.assertEqual(
            set(PostingCommand.objects.values_list('key', flat=True)),
            {'expense-paid:draft', 'expense-paid:payment'},
        )
        self.assertEqual(PostingCommand.objects.count(), 2)
        self.assertEqual(CashMovement.objects.filter(is_generated=True).count(), 1)
        movement = CashMovement.objects.get(is_generated=True)
        self.assertEqual(movement.financial_account_id, expense.financial_account_id)
        self.assertEqual(movement.financial_account, self.cash_account)

        findings = FinanceReconciler(
            start=expense.business_date, end=expense.business_date, scope='expenses'
        ).run()
        self.assertFalse(
            [finding for finding in findings if finding['code'] == 'expense_movement_mismatch']
        )

    def test_approved_post_uses_distinct_draft_and_approval_keys(self):
        response = self.client.post(
            self.url,
            self._payload(
                Expense.Status.APPROVED,
                payment_method=Expense.PaymentMethod.CREDIT,
                liability_account=str(self.liability_account.pk),
            ),
            HTTP_IDEMPOTENCY_KEY='expense-approved',
        )

        self.assertRedirects(
            response, reverse('staff_finance_expenses'), fetch_redirect_response=False
        )
        self.assertEqual(Expense.objects.get().status, Expense.Status.APPROVED)
        self.assertEqual(
            set(PostingCommand.objects.values_list('key', flat=True)),
            {'expense-approved:draft', 'expense-approved:approval'},
        )

    def test_invalid_post_renders_validation_errors_without_creating_expense(self):
        response = self.client.post(
            self.url, self._payload(Expense.Status.DRAFT, amount_syp='0', title='')
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['errors'])
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(PostingCommand.objects.count(), 0)


class ProductionLoggingConfigurationTests(TestCase):
    def test_request_errors_use_stderr_console_handler(self):
        request_logger = settings.LOGGING['loggers']['django.request']
        handler = settings.LOGGING['handlers']['console']
        self.assertEqual(request_logger['level'], 'ERROR')
        self.assertEqual(request_logger['handlers'], ['console'])
        self.assertEqual(handler['stream'], 'ext://sys.stderr')
        self.assertFalse(settings.DEBUG)


class PublicMenuOptionChipCssTests(TestCase):
    def test_selected_option_chip_uses_dark_text_and_tinted_surface(self):
        css = (settings.BASE_DIR / 'static/css/hub.css').read_text()
        rule = css.split('.public-menu-redesign .menu-option-chip:has(input:checked){', 1)[1].split('}', 1)[0]
        self.assertIn('color:var(--menu-ink)', rule)
        self.assertIn('color-mix(', rule)
        self.assertNotIn('color:#fff', rule)
