from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.currency import (SYP_NEW, USD, applicable_rate, convert_to_base, decimal_amount,
                           enforce_risk, evaluate_currency_risk, record_snapshot)
from core.models import ExchangeRate, Expense, ExpenseCategory


class CurrencySafetyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='currency-user', password='x')

    def test_decimal_conversion_and_arabic_numbers(self):
        self.assertEqual(decimal_amount('١٬٢٣٤٫٥٠'), Decimal('1234.50'))
        self.assertEqual(convert_to_base('100', USD, Decimal('130'))[0], Decimal('13000.00'))
        self.assertEqual(convert_to_base('1,000', SYP_NEW)[0], Decimal('1000.00'))

    def test_old_syp_warning_suggestion_and_acknowledgment(self):
        result = evaluate_currency_risk(amount='100000', currency=SYP_NEW, operation='expense')
        self.assertEqual(result.level, 'manager_review_required')
        self.assertEqual(result.suggested_amount, Decimal('1000.00'))
        self.assertIn('possible_old_syp_x100', result.reason_codes)
        with self.assertRaises(ValidationError): enforce_risk(result, user=self.user)
        with self.assertRaises(PermissionDenied): enforce_risk(result, acknowledged=True, user=self.user)

    def test_system_calculated_total_is_not_blocked(self):
        result = evaluate_currency_risk(amount='999999', manually_entered=False)
        self.assertEqual(result.level, 'normal')

    def test_rate_validation_rejects_zero_negative_and_reciprocal(self):
        for rate in ('0', '-130', '0.00769'):
            with self.assertRaises(ValidationError):
                ExchangeRate(rate_to_base=Decimal(rate), effective_date=date.today()).full_clean()

    @override_settings(CURRENCY_RATE_MAX_AGE_DAYS=2)
    def test_dated_future_missing_and_stale_rates(self):
        day = date(2030, 1, 10)
        ExchangeRate.objects.create(rate_to_base=130, effective_date=day - timedelta(days=3))
        ExchangeRate.objects.create(rate_to_base=140, effective_date=day + timedelta(days=1))
        with self.assertRaises(ValidationError): applicable_rate(day)
        self.assertEqual(applicable_rate(day, allow_stale=True, user=self._stale_user()).rate_to_base, Decimal('130'))
        with self.assertRaises(ValidationError): applicable_rate(date(2020, 1, 1))

    def _stale_user(self):
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(Permission.objects.get(codename='use_stale_exchange_rate'))
        return self.user

    def test_snapshot_preserves_historical_usd_rate(self):
        rate = ExchangeRate.objects.create(rate_to_base=Decimal('130'), effective_date=timezone.localdate(), source='daily')
        category = ExpenseCategory.objects.create(name_ar='اختبار', code='currency-test')
        expense = Expense.objects.create(business_date=timezone.localdate(), category=category, title='USD', amount_syp=1300)
        snapshot = record_snapshot(expense, operation='expense', field_name='amount_syp', amount='10', currency=USD,
                                   rate_record=rate, user=self.user)
        ExchangeRate.objects.create(rate_to_base=Decimal('150'), effective_date=timezone.localdate(), source='new')
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.original_amount, Decimal('10'))
        self.assertEqual(snapshot.exchange_rate_to_base, Decimal('130'))
        self.assertEqual(snapshot.base_amount_syp, Decimal('1300'))
