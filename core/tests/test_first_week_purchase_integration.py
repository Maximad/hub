"""Realistic first-week staging journey: draft → partial receipt → balance → finish.

Never creates production records. Django TestCase rolls back all test payments
and stock movements using the isolated PostgreSQL CI database.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CashMovement, FinancialAccount, InventoryItem, PostingBatch, PostingEntry,
    Purchase, PurchaseItem, PurchasePayment, PurchaseReceiptLine, StockMovement,
)


@override_settings(
    ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class FirstWeekPurchaseJourneyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='first-week-admin', password='unused',
            phone='+96319997701', role='admin',
        )
        self.kitchen = User.objects.create_user(
            username='first-week-kitchen', password='unused',
            phone='+96319997702', role='kitchen',
        )
        self.client.force_login(self.admin)
        self.item = InventoryItem.objects.create(
            name_ar='قهوة افتتاح', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('3.000'),
        )
        for code, account_type in (
            ('inventory:purchases', FinancialAccount.AccountType.ASSET),
            ('payable:suppliers', FinancialAccount.AccountType.LIABILITY),
        ):
            FinancialAccount.objects.create(
                code=code, name_ar=code, account_type=account_type,
                is_active=True,
            )

    def draft(self):
        response = self.client.post(
            reverse('staff_inventory_purchase_new'),
            {
                'business_date': str(timezone.localdate()),
                'supplier_name': 'مورد فعلي',
                'inventory_item': [str(self.item.pk), '', '', '', ''],
                'quantity': ['5', '', '', '', ''],
                'unit_cost_syp': ['100', '', '', '', ''],
                'discount_syp': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Purchase.objects.count(), 1)
        return Purchase.objects.get()

    def test_draft_partial_and_second_receipt_share_correct_remaining_quantity(self):
        purchase = self.draft()
        line = purchase.items.get()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(PurchasePayment.objects.count(), 0)
        self.assertEqual(CashMovement.objects.count(), 0)

        inventory_url = reverse('staff_inventory_purchase_detail', args=[purchase.pk])
        finance_url = reverse('staff_purchase_detail', args=[purchase.pk])
        inventory_receive = reverse('staff_inventory_purchase_receive', args=[purchase.pk])
        initial_inventory = self.client.get(inventory_url)
        initial_finance = self.client.get(finance_url)
        for result in (initial_inventory, initial_finance):
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.context['receipt_rows'][0]['received'], Decimal('0'))
            self.assertEqual(result.context['receipt_rows'][0]['remaining'], Decimal('5.000'))
            self.assertContains(result, 'name="quantity_{}"'.format(line.pk))
        self.assertNotContains(initial_finance, 'data-purchase-payment-form')
        self.assertContains(initial_finance, 'استلم المواد أولاً')

        first = self.client.post(inventory_receive, {f'quantity_{line.pk}': '2'})
        self.assertEqual(first.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('5.000'))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(PurchaseReceiptLine.objects.count(), 1)
        self.assertEqual(PurchasePayment.objects.count(), 0)

        for result in (self.client.get(inventory_url), self.client.get(finance_url)):
            with self.subTest(template=result.templates[0].name):
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.context['receipt_rows'][0]['ordered'], Decimal('5.000'))
                self.assertEqual(result.context['receipt_rows'][0]['received'], Decimal('2.000'))
                self.assertEqual(result.context['receipt_rows'][0]['remaining'], Decimal('3.000'))
                self.assertContains(result, 'value="3.000"')
                self.assertContains(result, 'max="3.000"')
        partial_finance = self.client.get(finance_url)
        self.assertContains(partial_finance, 'data-purchase-payment-form')
        self.assertEqual(partial_finance.context['financial_state']['outstanding'], Decimal('200.00'))

        second = self.client.post(finance_url, {
            'action': 'receive', f'quantity_{line.pk}': '3',
        })
        self.assertEqual(second.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('8.000'))
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(PurchaseReceiptLine.objects.count(), 2)
        self.assertEqual(PurchasePayment.objects.count(), 0)
        self.assertEqual(CashMovement.objects.count(), 0)
        self.assertEqual(PostingBatch.objects.filter(operation_type='purchase.receipt.liability').count(), 2)
        self.assertEqual(PostingEntry.objects.filter(batch__operation_type='purchase.receipt.liability').count(), 4)

        for result in (self.client.get(inventory_url), self.client.get(finance_url)):
            self.assertEqual(result.context['receipt_rows'][0]['remaining'], Decimal('0'))
            self.assertFalse(result.context['can_receive_purchase'])
            self.assertNotContains(result, 'name="quantity_{}"'.format(line.pk))
            self.assertContains(result, 'اكتمل استلام جميع المواد')
        self.assertEqual(self.client.get(finance_url).context['financial_state']['outstanding'], Decimal('500.00'))

    def test_cancelled_draft_does_not_offer_receive_pay_or_cancel_again(self):
        purchase = self.draft()
        Purchase.objects.filter(pk=purchase.pk).update(status=Purchase.Status.CANCELLED)
        for url in (
            reverse('staff_inventory_purchase_detail', args=[purchase.pk]),
            reverse('staff_purchase_detail', args=[purchase.pk]),
        ):
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200)
            self.assertNotContains(result, 'name="quantity_')
            self.assertContains(result, 'ملغى')
        finance = self.client.get(reverse('staff_purchase_detail', args=[purchase.pk]))
        self.assertNotContains(finance, 'data-purchase-payment-form')
        self.assertNotContains(finance, 'value="cancel"')
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_kitchen_inventory_view_is_read_only_for_purchase_receipt(self):
        purchase = self.draft()
        detail = reverse('staff_inventory_purchase_detail', args=[purchase.pk])
        self.client.force_login(self.kitchen)
        result = self.client.get(detail)
        self.assertEqual(result.status_code, 200)
        self.assertNotContains(result, 'name="quantity_')
        self.assertContains(result, 'الاستلام يحتاج صلاحية تشغيل وكاشير')
        self.assertNotContains(result, 'تسجيل دفعة المورد / عرض التسويات')
        blocked = self.client.post(
            reverse('staff_inventory_purchase_receive', args=[purchase.pk]),
            {f'quantity_{purchase.items.get().pk}': '5'},
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(PurchasePayment.objects.count(), 0)
