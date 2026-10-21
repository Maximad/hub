"""Converted inventory units are input convenience only; posting remains base-unit."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    InventoryItem, Purchase, PurchaseReceiptLine, StockMovement, Payment,
    FinancialAccount, PostingBatch,
)
from core.services.inventory_unit_entry import (
    allowed_entry_units, to_base_quantity, to_base_unit_cost, receipt_quantities_from_post,
)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class UnitConversionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username='inventory-units-admin', phone='982711', password='unused', role='admin'
        )
        cls.gr = InventoryItem.objects.create(
            name_ar='طحين', unit=InventoryItem.Unit.G, current_quantity=Decimal('500.000'),
        )
        cls.ml = InventoryItem.objects.create(
            name_ar='حليب', unit=InventoryItem.Unit.ML, current_quantity=Decimal('200.000'),
        )
        cls.piece = InventoryItem.objects.create(
            name_ar='زجاجات ماء', unit=InventoryItem.Unit.PIECE, current_quantity=Decimal('10.000'),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_unit_math_and_disallow_guessing_package_size(self):
        for qty, base, entered, expected in [
            ('1.5', 'g', 'kg', '1500.000'),
            ('750', 'kg', 'g', '0.750'),
            ('1.25', 'ml', 'liter', '1250.000'),
            ('750', 'liter', 'ml', '0.750'),
            ('1.5', 'g', '', '1.500'),
        ]:
            with self.subTest(qty=qty, base=base, entered=entered):
                self.assertEqual(to_base_quantity(qty, base, entered), Decimal(expected))
        self.assertEqual(allowed_entry_units('piece'), ('piece',))
        for raw, base, unit in [
            ('1', 'piece', 'box'), ('1', 'ml', 'kg'), ('0.0001', 'kg', 'kg'),
            ('-1', 'g', 'g'), ('NaN', 'g', 'kg'), ('Infinity', 'g', 'kg'),
        ]:
            with self.subTest(raw=raw, base=base, unit=unit):
                with self.assertRaises(ValidationError):
                    to_base_quantity(raw, base, unit)
        self.assertEqual(to_base_quantity('0', 'kg', 'g', allow_zero=True), Decimal('0.000'))

    def test_money_conversion_preserves_supplier_line_total_or_rejects(self):
        self.assertEqual(to_base_unit_cost('6000', 'g', 'kg'), Decimal('6.00'))
        self.assertEqual(to_base_unit_cost('0.60', 'kg', 'g'), Decimal('600.00'))
        with self.assertRaises(ValidationError):
            to_base_unit_cost('25', 'g', 'kg')  # would need 0.025 per gram
        with self.assertRaises(ValidationError):
            to_base_unit_cost('-1', 'g', 'kg')

    def post_draft(self, item, *, quantity, entry_unit, price):
        return self.client.post(reverse('staff_inventory_purchase_new'), {
            'business_date': str(timezone.localdate()),
            'supplier_name': 'مورد اختبار',
            'inventory_item': [str(item.pk)],
            'quantity': [quantity],
            'quantity_unit': [entry_unit],
            'unit_cost_syp': [price],
            'discount_syp': '0',
        })

    def test_purchase_kg_enters_grams_base_cost_and_does_not_receive(self):
        view = self.client.get(reverse('staff_inventory_purchase_new'))
        self.assertContains(view, 'name="quantity_unit"')
        self.assertContains(view, 'data-base-unit="g"')
        self.assertContains(view, 'hub_purchase_draft.js')
        response = self.post_draft(self.gr, quantity='1.5', entry_unit='kg', price='6000')
        self.assertEqual(response.status_code, 302)
        purchase = Purchase.objects.get()
        line = purchase.items.get()
        self.assertEqual(line.quantity, Decimal('1500.000'))
        self.assertEqual(line.unit, InventoryItem.Unit.G)
        self.assertEqual(line.unit_cost_syp, Decimal('6.00'))
        self.assertEqual(line.line_total_syp, Decimal('9000'))
        self.gr.refresh_from_db()
        self.assertEqual(self.gr.current_quantity, Decimal('500.000'))
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_invalid_unit_or_unrepresentable_cost_fails_without_draft(self):
        for entry, price in [('piece', '6000'), ('kg', '25'), ('kg', '-1')]:
            with self.subTest(entry=entry, price=price):
                response = self.post_draft(self.gr, quantity='1.5', entry_unit=entry, price=price)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['errors'])
                self.assertEqual(Purchase.objects.count(), 0)
                self.assertEqual(StockMovement.objects.count(), 0)

    def test_stock_count_preview_signed_base_unit_and_single_confirm(self):
        url = reverse('staff_inventory_stock_count')
        form = self.client.get(url, {'item': self.gr.pk})
        self.assertContains(form, 'name="count_unit"')
        preview = self.client.post(url, {
            'action': 'preview', 'item': self.gr.pk, 'count': '1.5',
            'count_unit': 'kg', 'reason': 'جرد فعلي',
        })
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context['preview']['after'], Decimal('1500.000'))
        self.assertEqual(preview.context['preview']['difference'], Decimal('1000.000'))
        self.assertEqual(preview.context['preview']['entered_unit_label'], 'كغ')
        token = preview.context['preview']['token']
        self.assertEqual(self.client.post(url, {'action': 'confirm', 'confirmation_token': token}).status_code, 302)
        self.assertEqual(self.client.post(url, {'action': 'confirm', 'confirmation_token': token}).status_code, 302)
        self.gr.refresh_from_db()
        self.assertEqual(self.gr.current_quantity, Decimal('1500.000'))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(StockMovement.objects.get().quantity, Decimal('1000.000'))

    def test_stock_count_wrong_dimensional_unit_fails_without_mutation(self):
        url = reverse('staff_inventory_stock_count')
        res = self.client.post(url, {
            'action': 'preview', 'item': self.ml.pk,
            'count': '1', 'count_unit': 'kg', 'reason': 'جرد',
        })
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context['preview'])
        self.assertFalse(StockMovement.objects.exists())

    def test_purchase_receipt_kg_equivalence_partial_and_finance_completion(self):
        for code, account_type in (
            ('inventory:purchases', FinancialAccount.AccountType.ASSET),
            ('payable:suppliers', FinancialAccount.AccountType.LIABILITY),
        ):
            FinancialAccount.objects.update_or_create(
                code=code,
                defaults={'name_ar': code, 'account_type': account_type, 'is_active': True},
            )
        self.assertEqual(self.post_draft(self.gr, quantity='2.5', entry_unit='kg', price='6000').status_code, 302)
        purchase = Purchase.objects.get()
        line = purchase.items.get()
        page = self.client.get(reverse('staff_inventory_purchase_detail', args=[purchase.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'name="quantity_unit_{line.pk}"')
        self.assertContains(page, 'hub_receipt_units.js')
        inventory_receive = reverse('staff_inventory_purchase_receive', args=[purchase.pk])
        first = self.client.post(inventory_receive, {
            f'quantity_{line.pk}': '1.5',
            f'quantity_unit_{line.pk}': 'kg',
        })
        self.assertEqual(first.status_code, 302)
        self.gr.refresh_from_db()
        self.assertEqual(self.gr.current_quantity, Decimal('2000.000'))
        self.assertEqual(PurchaseReceiptLine.objects.count(), 1)
        self.assertEqual(PurchaseReceiptLine.objects.get().received_quantity, Decimal('1500.000'))
        finance = self.client.post(reverse('staff_purchase_detail', args=[purchase.pk]), {
            'action': 'receive', f'quantity_{line.pk}': '1000',
            f'quantity_unit_{line.pk}': 'g',
        })
        self.assertEqual(finance.status_code, 302)
        self.gr.refresh_from_db()
        self.assertEqual(self.gr.current_quantity, Decimal('3000.000'))
        self.assertEqual(PurchaseReceiptLine.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(PostingBatch.objects.filter(operation_type='purchase.receipt.liability').count(), 2)

    def test_receipt_invalid_unit_fails_closed(self):
        self.post_draft(self.ml, quantity='2', entry_unit='liter', price='1000')
        purchase = Purchase.objects.get()
        line = purchase.items.get()
        response = self.client.post(reverse('staff_inventory_purchase_receive', args=[purchase.pk]), {
            f'quantity_{line.pk}': '2',
            f'quantity_unit_{line.pk}': 'kg',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'غير متوافقة')
        self.ml.refresh_from_db()
        self.assertEqual(self.ml.current_quantity, Decimal('200.000'))
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(PurchaseReceiptLine.objects.count(), 0)
