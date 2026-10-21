"""Staff retry journeys use original transactional receipts, with no payments."""
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (
    ActivityLog, AuditEvent, CashMovement, FinancialAccount, InventoryItem, PostingBatch,
    PostingCommand, PostingEntry, Purchase, PurchaseItem, PurchasePayment,
    PurchaseReceipt, PurchaseReceiptLine, StockMovement,
)


@override_settings(
    ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class PurchaseReceiptRecoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username='receipt-admin', phone='972001', role='admin')
        cls.cashier = User.objects.create_user(username='receipt-cashier', phone='972002', role='cashier')
        cls.kitchen = User.objects.create_user(username='receipt-kitchen', phone='972003', role='kitchen')
        for code, kind in (
            ('inventory:purchases', FinancialAccount.AccountType.ASSET),
            ('payable:suppliers', FinancialAccount.AccountType.LIABILITY),
        ):
            FinancialAccount.objects.update_or_create(code=code, defaults={
                'name_ar': code, 'account_type': kind, 'is_active': True,
            })

    def setUp(self):
        self.client.force_login(self.admin)

    def purchase(self):
        purchase = Purchase.objects.create(business_date=timezone.localdate(), supplier_name='مورد فعلي')
        for name, quantity, stock in [('قهوة', '2000', '10'), ('سكر', '500', '20')]:
            item = InventoryItem.objects.create(name_ar=name, unit='g', current_quantity=stock)
            PurchaseItem.objects.create(purchase=purchase, inventory_item=item, quantity=Decimal(quantity), unit='g', unit_cost_syp=Decimal('1'))
        purchase.recalculate_totals()
        purchase.save()
        return purchase

    def url(self, purchase, mode, *, submit=False):
        route = ('staff_inventory_purchase_receive' if submit else 'staff_inventory_purchase_detail') if mode == 'inventory' else 'staff_purchase_detail'
        return reverse(route, args=[purchase.pk])

    def form(self, purchase, mode):
        response = self.client.get(self.url(purchase, mode))
        self.assertEqual(response.status_code, 200)
        token = response.context['receipt_token']
        self.assertEqual(str(UUID(token)), token)
        self.assertContains(response, f'name="receipt_token" value="{token}"')
        return token

    def data(self, purchase, token, quantity='500', unit='g'):
        first, second = purchase.items.order_by('pk')
        return {'action': 'receive', 'receipt_token': token,
                f'quantity_{first.pk}': quantity, f'quantity_unit_{first.pk}': unit,
                f'quantity_{second.pk}': '0', f'quantity_unit_{second.pk}': 'g'}

    def snapshot(self):
        models = (PurchaseReceipt, PurchaseReceiptLine, StockMovement, PostingCommand,
                  PostingBatch, PostingEntry, AuditEvent, ActivityLog, PurchasePayment, CashMovement)
        return ([model.objects.count() for model in models],
                list(InventoryItem.objects.order_by('pk').values_list('current_quantity', flat=True)),
                list(Purchase.objects.order_by('pk').values_list('status', 'received_at')))

    def assert_receipts(self, purchase, count, first_stock):
        first, second = purchase.items.order_by('pk')
        first.inventory_item.refresh_from_db()
        second.inventory_item.refresh_from_db()
        self.assertEqual(first.inventory_item.current_quantity, Decimal(first_stock))
        self.assertEqual(second.inventory_item.current_quantity, Decimal('20'))
        self.assertEqual(purchase.receipts.count(), count)
        self.assertEqual(PurchaseReceiptLine.objects.filter(receipt__purchase=purchase).count(), count)
        self.assertEqual(StockMovement.objects.filter(related_purchase=purchase).count(), count)
        self.assertEqual(purchase.payments.count(), 0)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_excess_receipt_keeps_unit_quantities_and_token_then_corrects_once(self):
        for mode in ('inventory', 'finance'):
            with self.subTest(mode=mode):
                purchase = self.purchase()
                token = self.form(purchase, mode)
                data = self.data(purchase, token, '3', 'kg')
                before = self.snapshot()
                response = self.client.post(self.url(purchase, mode, submit=True), data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'تتجاوز المتبقي')
                self.assertContains(response, 'role="alert"')
                self.assertContains(response, 'value="3"')
                self.assertContains(response, 'value="kg" selected')
                self.assertContains(response, 'max="2.000"')
                self.assertEqual(response.context['receipt_rows'][1]['entered_quantity'], '0')
                self.assertEqual(response.context['receipt_token'], token)
                self.assertEqual(self.snapshot(), before)
                first = purchase.items.order_by('pk').first()
                data[f'quantity_{first.pk}'] = '1.5'
                for _ in range(2):
                    self.assertEqual(self.client.post(self.url(purchase, mode, submit=True), data).status_code, 302)
                self.assert_receipts(purchase, 1, '1510')

    def test_invalid_units_stay_invalid_until_explicitly_corrected(self):
        for mode in ('inventory', 'finance'):
            purchase = self.purchase()
            token = self.form(purchase, mode)
            data = self.data(purchase, token, '2', 'box')
            before = self.snapshot()
            response = self.client.post(self.url(purchase, mode, submit=True), data)
            self.assertContains(response, 'غير متوافقة')
            self.assertContains(response, 'value="box" selected')
            self.assertEqual(response.context['receipt_rows'][0]['entered_quantity'], '2')
            self.assertEqual(self.snapshot(), before)
            first = purchase.items.order_by('pk').first()
            data[f'quantity_unit_{first.pk}'] = 'g'
            self.assertEqual(self.client.post(self.url(purchase, mode, submit=True), data).status_code, 302)
            self.assert_receipts(purchase, 1, '12')

    def test_invalid_quantities_and_unknown_lines_leave_no_artifacts(self):
        for mode in ('inventory', 'finance'):
            purchase = self.purchase()
            for quantity in ('', 'NaN', '-1', '0.0001', '0'):
                with self.subTest(mode=mode, quantity=quantity):
                    before = self.snapshot()
                    response = self.client.post(self.url(purchase, mode, submit=True), self.data(purchase, self.form(purchase, mode), quantity))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context['receipt_errors'])
                    self.assertEqual(self.snapshot(), before)
            data = self.data(purchase, self.form(purchase, mode))
            data['quantity_999999999'] = '1'
            before = self.snapshot()
            response = self.client.post(self.url(purchase, mode, submit=True), data)
            self.assertContains(response, 'بند استلام غير معروف')
            self.assertEqual(self.snapshot(), before)

    def test_missing_line_defaults_to_zero_on_retry_instead_of_full_remaining(self):
        purchase = self.purchase()
        first, second = purchase.items.order_by('pk')
        data = {'receipt_token': self.form(purchase, 'inventory'), f'quantity_{first.pk}': '3000'}
        response = self.client.post(self.url(purchase, 'inventory', submit=True), data)
        self.assertEqual(response.context['receipt_rows'][1]['entered_quantity'], '0')
        data[f'quantity_{first.pk}'] = '500'
        data[f'quantity_{second.pk}'] = response.context['receipt_rows'][1]['entered_quantity']
        self.assertEqual(self.client.post(self.url(purchase, 'inventory', submit=True), data).status_code, 302)
        self.assert_receipts(purchase, 1, '510')

    def test_equal_partial_deliveries_and_cross_screen_retries(self):
        self.client.force_login(self.cashier)
        for mode, other in (('inventory', 'finance'), ('finance', 'inventory')):
            purchase = self.purchase()
            data = self.data(purchase, self.form(purchase, mode))
            for destination in (mode, other, mode):
                self.assertEqual(self.client.post(self.url(purchase, destination, submit=True), data).status_code, 302)
            self.assert_receipts(purchase, 1, '510')
            new_token = self.form(purchase, other)
            self.assertNotEqual(new_token, data['receipt_token'])
            data['receipt_token'] = new_token
            for _ in range(2):
                self.assertEqual(self.client.post(self.url(purchase, other, submit=True), data).status_code, 302)
            self.assert_receipts(purchase, 2, '1010')

    def test_receipt_token_cannot_alias_another_purchase(self):
        first, second = self.purchase(), self.purchase()
        token = self.form(first, 'inventory')
        for purchase in (first, second):
            self.assertEqual(self.client.post(self.url(purchase, 'inventory', submit=True), self.data(purchase, token)).status_code, 302)
            self.assert_receipts(purchase, 1, '510')

    def test_invalid_token_is_rejected_and_replaced_for_correction(self):
        for mode in ('inventory', 'finance'):
            purchase = self.purchase()
            before = self.snapshot()
            response = self.client.post(self.url(purchase, mode, submit=True), self.data(purchase, 'invalid'))
            self.assertContains(response, 'مرجع محاولة الاستلام غير صالح')
            self.assertEqual(str(UUID(response.context['receipt_token'])), response.context['receipt_token'])
            self.assertEqual(self.snapshot(), before)

    def test_posting_failure_rolls_back_stock_and_ledger_then_retries_once(self):
        for mode in ('inventory', 'finance'):
            purchase = self.purchase()
            data = self.data(purchase, self.form(purchase, mode))
            before = self.snapshot()
            with patch('core.services.posting.purchases._audit', side_effect=RuntimeError('private database detail')), self.assertLogs('core.services.purchase_receipt_ui', level='ERROR'):
                response = self.client.post(self.url(purchase, mode, submit=True), data)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'أعد المحاولة بالقيم نفسها')
            self.assertNotContains(response, 'private database detail')
            self.assertEqual(response.context['receipt_token'], data['receipt_token'])
            self.assertEqual(self.snapshot(), before)
            for _ in range(2):
                self.assertEqual(self.client.post(self.url(purchase, mode, submit=True), data).status_code, 302)
            self.assert_receipts(purchase, 1, '510')

    def test_failure_on_second_line_rolls_back_first_line_too(self):
        for mode in ('inventory', 'finance'):
            purchase = self.purchase()
            first, second = purchase.items.order_by('pk')
            data = self.data(purchase, self.form(purchase, mode))
            data[f'quantity_{second.pk}'] = '501'
            before = self.snapshot()
            response = self.client.post(self.url(purchase, mode, submit=True), data)
            self.assertContains(response, 'تتجاوز المتبقي')
            self.assertEqual(response.context['receipt_rows'][0]['entered_quantity'], '500')
            self.assertEqual(self.snapshot(), before)

    def test_revoked_permission_blocks_new_receipt_and_duplicate_retry(self):
        self.client.force_login(self.cashier)
        for mode, capability in (('inventory', 'inventory'), ('finance', 'finance')):
            purchase = self.purchase()
            data = self.data(purchase, self.form(purchase, mode))
            self.assertEqual(self.client.post(self.url(purchase, mode, submit=True), data).status_code, 302)
            UserCapabilityOverride.objects.create(user=self.cashier, capability=capability, allowed=False)
            before = self.snapshot()
            self.assertEqual(self.client.post(self.url(purchase, mode, submit=True), data).status_code, 302)
            self.assertEqual(self.snapshot(), before)
            UserCapabilityOverride.objects.filter(user=self.cashier, capability=capability).delete()

    def test_read_only_inventory_user_never_sees_or_submits_receipt_form(self):
        purchase = self.purchase()
        token = self.form(purchase, 'inventory')
        self.client.force_login(self.kitchen)
        response = self.client.get(self.url(purchase, 'inventory'))
        self.assertNotContains(response, 'name="receipt_token"')
        self.assertNotContains(response, 'name="quantity_')
        before = self.snapshot()
        self.assertEqual(self.client.post(self.url(purchase, 'inventory', submit=True), self.data(purchase, token)).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_get_receive_url_and_cancelled_purchase_do_not_write(self):
        for mode in ('inventory', 'finance'):
            purchase = self.purchase()
            data = self.data(purchase, self.form(purchase, mode))
            before = self.snapshot()
            self.client.get(self.url(purchase, mode, submit=True))
            self.assertEqual(self.snapshot(), before)
            Purchase.objects.filter(pk=purchase.pk).update(status=Purchase.Status.CANCELLED)
            before = self.snapshot()
            self.client.post(self.url(purchase, mode, submit=True), data)
            self.assertEqual(self.snapshot(), before)
