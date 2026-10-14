"""Pre-launch purchase UX: recording a draft is not receipt or supplier payment."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import InventoryItem, Purchase, PurchasePayment, StockMovement


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class LaunchPurchaseGuidanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='purchase-guidance-admin', password='unused',
            phone='+9631999444', role='admin',
        )
        self.client.force_login(self.user)
        self.item = InventoryItem.objects.create(
            name_ar='قهوة للاختبار', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('3'),
        )

    def test_inventory_purchase_entry_does_not_offer_unprocessed_payment_field(self):
        response = self.client.get(reverse('staff_inventory_purchase_new'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الحفظ وحده لا يضيف كمية إلى المخزون')
        self.assertContains(response, 'حفظ الشراء كمسودة')
        self.assertContains(response, 'data-hub-purchase-draft')
        self.assertContains(response, 'data-purchase-item')
        self.assertContains(response, 'data-purchase-total')
        self.assertContains(response, 'hub_purchase_draft.js')
        self.assertNotContains(response, 'name="amount_paid_syp"')
        self.assertNotContains(response, 'name="status"')
        self.assertNotContains(response, 'name="payment_method"')
        self.assertNotContains(response, 'name="paid_from"')

    def test_finance_purchase_entry_starts_as_draft_and_avoids_fake_payment(self):
        response = self.client.get(reverse('staff_purchase_new'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'سيُحفظ الشراء كمسودة')
        self.assertNotContains(response, 'name="amount_paid_syp"')
        self.assertNotContains(response, 'name="payment_method"')
        self.assertNotContains(response, 'name="paid_from"')

    def test_purchase_line_errors_preserve_entries_without_creating_draft(self):
        response = self.client.post(reverse('staff_inventory_purchase_new'), {
            'business_date': '2026-10-14',
            'supplier_name': 'مورد الاختبار',
            'inventory_item': [str(self.item.pk), '', '', '', ''],
            'quantity': ['', '', '', '', ''],
            'unit_cost_syp': ['123', '', '', '', ''],
            'notes': 'سنسجل الكمية لاحقاً',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'أكمل المادة والكمية والكلفة')
        self.assertContains(response, 'مورد الاختبار')
        self.assertContains(response, 'سنسجل الكمية لاحقاً')
        self.assertEqual(Purchase.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_purchase_missing_item_or_invalid_cost_fails_atomically(self):
        for items, qty, cost in [
            (['', '', '', '', ''], ['', '', '', '', ''], ['', '', '', '', '']),
            ([str(self.item.pk)], ['2'], ['-10']),
            ([str(self.item.pk)], ['bad'], ['100']),
        ]:
            with self.subTest(items=items, qty=qty, cost=cost):
                response = self.client.post(reverse('staff_inventory_purchase_new'), {
                    'business_date': '2026-10-14',
                    'inventory_item': items,
                    'quantity': qty,
                    'unit_cost_syp': cost,
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['errors'])
                self.assertEqual(Purchase.objects.count(), 0)
                self.assertEqual(StockMovement.objects.count(), 0)

    def test_inventory_purchase_save_does_not_receive_stock_or_post_payment(self):
        response = self.client.post(
            reverse('staff_inventory_purchase_new'),
            {
                'business_date': '2026-10-14',
                'supplier_name': 'مورد اختبار',
                'inventory_item': [str(self.item.pk)],
                'quantity': ['2'],
                'unit_cost_syp': ['100'],
                # A forged legacy form value must not cause a payment.
                'amount_paid_syp': '200',
                'status': Purchase.Status.PAID,
                'payment_method': Purchase.PaymentMethod.CASH,
                'paid_from': Purchase.PaidFrom.CASHBOX,
            },
        )
        self.assertEqual(response.status_code, 302)
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertEqual(purchase.amount_paid_syp, 0)
        self.assertEqual(purchase.payment_method, Purchase.PaymentMethod.CREDIT)
        self.assertEqual(purchase.paid_from, Purchase.PaidFrom.UNPAID)
        self.assertEqual(purchase.items.count(), 1)
        self.assertEqual(purchase.items.first().quantity, Decimal('2'))
        self.assertEqual(PurchasePayment.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('3'))

        details = self.client.get(
            reverse('staff_inventory_purchase_detail', args=[purchase.pk])
        )
        self.assertEqual(details.status_code, 200)
        self.assertContains(details, 'تسجيل دفعة المورد / عرض التسويات')
        self.assertContains(details, 'تأكيد الاستلام وتحديث المخزون')
        self.assertContains(
            details, reverse('staff_purchase_detail', args=[purchase.pk])
        )
