from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import FinancialAccount
from core.services.posting.exceptions import InvalidTransition
from core.services.posting.purchases import _account


class ApprovedPurchaseFinanceDecisionTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='finance-policy-test', password='pass', phone='+9907', role='admin'
        )

    def test_missing_account_is_blocked_and_not_created(self):
        with self.assertRaises(KeyError):
            _account('inventory:unmapped')
        self.assertFalse(FinancialAccount.objects.filter(code='inventory:unmapped').exists())

    def test_inactive_candidate_account_is_blocked(self):
        FinancialAccount.objects.update_or_create(
            code='inventory:purchases', defaults={'name_ar': 'مشتريات مخزون',
            'account_type': FinancialAccount.AccountType.ASSET, 'is_active': False},
        )
        with self.assertRaisesRegex(InvalidTransition, 'مفقود أو غير فعّال'):
            _account('inventory:purchases')

    def test_wrong_account_type_is_blocked(self):
        FinancialAccount.objects.update_or_create(
            code='inventory:purchases', defaults={'name_ar': 'مشتريات مخزون',
            'account_type': FinancialAccount.AccountType.EXPENSE, 'is_active': True},
        )
        with self.assertRaisesRegex(InvalidTransition, 'نوعه/نطاقه غير صالح'):
            _account('inventory:purchases')
