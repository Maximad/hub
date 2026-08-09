from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from catalog.models import MediaAsset
from core.models import ExpenseCategory, FinancialAccount
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
