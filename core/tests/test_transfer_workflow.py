from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import CashMovement, FinancialAccount, PostingBatch, Transfer
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import InvalidTransition
from core.services.posting.transfers import post, reverse


class TransferPostingTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.actor=User.objects.create_user(username='cashier-transfer', password='x', phone='+99101')
        self.approver=User.objects.create_user(username='manager-transfer', password='x', phone='+99102', role='admin')
        self.source=FinancialAccount.objects.create(code='CASH-A', name_ar='الصندوق أ', account_type='asset', is_active=True)
        self.destination=FinancialAccount.objects.create(code='CASH-B', name_ar='الصندوق ب', account_type='asset', is_active=True)

    def context(self, key, approver=None):
        return PostingContext(actor=self.actor, approver=approver, business_date=date(2026, 8, 7), idempotency_key=key, channel='test')

    @override_settings(TRANSFER_APPROVAL_LIMIT_SYP=1000)
    def test_posts_one_balanced_batch_and_linked_projections(self):
        transfer=Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('500'), business_date=date(2026, 8, 7), reason='تغذية الصندوق')
        result=post(transfer, self.context('transfer:one'))
        self.assertEqual(result.state, Transfer.State.POSTED)
        self.assertTrue(result.posting_batch.is_balanced())
        projections=list(result.movement_projections.order_by('transfer_leg'))
        self.assertEqual(len(projections), 2)
        self.assertEqual({row.transfer_id for row in projections}, {result.pk})
        self.assertEqual({row.direction for row in projections}, {CashMovement.Direction.IN, CashMovement.Direction.OUT})
        self.assertTrue(all(row.is_generated for row in projections))

    @override_settings(TRANSFER_APPROVAL_LIMIT_SYP=1000)
    def test_limit_requires_a_different_approver(self):
        transfer=Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('1000'), business_date=date(2026, 8, 7), reason='تحويل كبير')
        with self.assertRaises(InvalidTransition):
            post(transfer, self.context('transfer:no-approval'))
        posted=post(transfer, self.context('transfer:approved', self.approver))
        self.assertEqual(posted.approver, self.approver)

    def test_reversal_is_one_inverse_batch_and_two_more_projections(self):
        transfer=post(Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('100'), business_date=date(2026, 8, 7), reason='مناقلة'), self.context('transfer:post'))
        transfer=reverse(transfer, self.context('transfer:reverse'), 'إعادة المبلغ')
        self.assertEqual(transfer.state, Transfer.State.REVERSED)
        self.assertEqual(transfer.reversal_batch.reversal_of_id, transfer.posting_batch_id)
        self.assertTrue(transfer.reversal_batch.is_balanced())
        self.assertEqual(transfer.movement_projections.count(), 4)
        self.assertEqual(PostingBatch.objects.filter(reversal_of=transfer.posting_batch).count(), 1)
