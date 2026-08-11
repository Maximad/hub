from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse as url_reverse

from core.models import CashMovement, FinancialAccount, PostingBatch, Transfer
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import InvalidTransition
from core.services.posting.transfers import post, reverse


class TransferPostingTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.actor=User.objects.create_user(username='finance-transfer', password='x', phone='+99101', role='admin')
        self.approver=User.objects.create_user(username='manager-transfer', password='x', phone='+99102', role='admin')
        self.source=FinancialAccount.objects.create(code='CASH-A', name_ar='الصندوق أ', account_type='asset', is_active=True)
        self.destination=FinancialAccount.objects.create(code='CASH-B', name_ar='الصندوق ب', account_type='asset', is_active=True)

    def context(self, key, approver=None):
        return PostingContext(actor=self.actor, approver=approver, business_date=date(2026, 8, 7), idempotency_key=key, channel='test')

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

    def test_49999_99_does_not_require_a_second_approver(self):
        transfer=Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('49999.99'), business_date=date(2026, 8, 7), reason='أقل من الحد')
        posted=post(transfer, self.context('transfer:below-boundary'))
        self.assertIsNone(posted.approver)

    def test_50000_requires_a_different_admin_approver(self):
        transfer=Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('50000'), business_date=date(2026, 8, 7), reason='تحويل كبير')
        with self.assertRaises(InvalidTransition):
            post(transfer, self.context('transfer:no-approval'))
        with self.assertRaisesRegex(InvalidTransition, 'مختلفاً'):
            post(transfer, self.context('transfer:self-approval', self.actor))
        posted=post(transfer, self.context('transfer:approved', self.approver))
        self.assertEqual(posted.approver, self.approver)

    def test_retry_returns_original_transfer_without_duplicate_sides(self):
        transfer=Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('100'), business_date=date(2026, 8, 7), reason='مناقلة')
        first=post(transfer, self.context('transfer:retry'))
        retried=post(transfer, self.context('transfer:retry'))
        self.assertEqual(retried.pk, first.pk)
        self.assertEqual(first.movement_projections.count(), 2)
        self.assertEqual(PostingBatch.objects.filter(operation_type='transfer.post').count(), 1)

    def test_accounts_must_share_business_unit_scope(self):
        self.source.business_unit='cafe'; self.source.save(update_fields=['business_unit'])
        self.destination.business_unit='events'; self.destination.save(update_fields=['business_unit'])
        transfer=Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('100'), business_date=date(2026, 8, 7), reason='نطاق خاطئ')
        with self.assertRaisesRegex(InvalidTransition, 'وحدة العمل نفسها'):
            post(transfer, self.context('transfer:scope'))

    def test_reversal_is_one_inverse_batch_and_two_more_projections(self):
        transfer=post(Transfer(source_account=self.source, destination_account=self.destination, amount=Decimal('100'), business_date=date(2026, 8, 7), reason='مناقلة'), self.context('transfer:post'))
        transfer=reverse(transfer, self.context('transfer:reverse'), 'إعادة المبلغ')
        self.assertEqual(transfer.state, Transfer.State.REVERSED)
        self.assertEqual(transfer.reversal_batch.reversal_of_id, transfer.posting_batch_id)
        self.assertTrue(transfer.reversal_batch.is_balanced())
        self.assertEqual(transfer.movement_projections.count(), 4)
        self.assertEqual(PostingBatch.objects.filter(reversal_of=transfer.posting_batch).count(), 1)
        with self.assertRaises(InvalidTransition):
            reverse(transfer, self.context('transfer:reverse-again'), 'محاولة ثانية')
        transfer.refresh_from_db()
        self.assertEqual(transfer.movement_projections.count(), 4)

    def test_staff_transfer_routes_use_integer_primary_keys(self):
        transfer = post(
            Transfer(
                source_account=self.source, destination_account=self.destination,
                amount=Decimal('100'), business_date=date(2026, 8, 7), reason='مناقلة',
            ),
            self.context('transfer:routes'),
        )
        self.client.force_login(self.approver)

        detail_url = url_reverse('staff_finance_transfer_detail', args=[transfer.pk])
        reverse_url = url_reverse('staff_finance_transfer_reverse', args=[transfer.pk])
        self.assertEqual(detail_url, f'/staff/finance/transfers/{transfer.pk}/')
        self.assertEqual(reverse_url, f'/staff/finance/transfers/{transfer.pk}/reverse/')
        self.assertEqual(resolve(detail_url).kwargs['transfer_id'], transfer.pk)
        self.assertEqual(resolve(reverse_url).kwargs['transfer_id'], transfer.pk)
        self.assertEqual(self.client.get('/staff/finance/transfers/999999999/').status_code, 404)
        self.assertEqual(self.client.get('/staff/finance/transfers/not-an-integer/').status_code, 404)
