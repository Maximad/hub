import csv
import tempfile
from pathlib import Path
from uuid import UUID

from django.core.management import call_command
from django.test import TestCase

from core.models import ActivityLog, Category, Order, OrderItem, Payment, Product


class NormalizeLegacyProductPricesTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='اختبار')

    def test_apply_divides_old_lira_price_once_and_keeps_monthly_internet(self):
        product = Product.objects.create(category=self.category, name_ar='منتج قديم', price_syp=155000)
        monthly = Product.objects.create(
            category=self.category,
            name_ar='شهر — ١٠٠ ساعة',
            price_syp=3000,
            public_code=UUID('c472c751-43cd-4a30-8f49-164383260905'),
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
        )

        call_command('normalize_legacy_product_prices', '--apply')
        product.refresh_from_db()
        monthly.refresh_from_db()
        self.assertEqual(product.price_syp, 1550)
        self.assertEqual(monthly.price_syp, 3000)
        self.assertTrue(ActivityLog.objects.filter(action='legacy_currency_price_normalized').exists())

        call_command('normalize_legacy_product_prices', '--apply')
        product.refresh_from_db()
        self.assertEqual(product.price_syp, 1550)

    def test_preview_does_not_change_prices(self):
        product = Product.objects.create(category=self.category, name_ar='منتج قديم', price_syp=20000)
        call_command('normalize_legacy_product_prices')
        product.refresh_from_db()
        self.assertEqual(product.price_syp, 20000)


class ImportPreSystemSalesTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(category=self.category, name_ar='قهوة عربية', price_syp=100)

    def _csv(self):
        handle = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8-sig', newline='')
        writer = csv.DictWriter(handle, fieldnames=[
            'month', 'product_public_code', 'raw_name', 'quantity', 'unit_price_syp', 'review_status', 'notes'
        ])
        writer.writeheader()
        writer.writerow({
            'month': '2026-07',
            'product_public_code': str(self.product.public_code),
            'raw_name': 'قهوة',
            'quantity': '17',
            'unit_price_syp': '100',
            'review_status': 'needs_review',
            'notes': 'عدد سعر الجيران غير معروف',
        })
        handle.close()
        return Path(handle.name)

    def test_preview_is_read_only(self):
        path = self._csv()
        try:
            call_command('import_pre_system_sales', '--file', str(path))
            self.assertEqual(Order.objects.count(), 0)
            self.assertEqual(OrderItem.objects.count(), 0)
        finally:
            path.unlink(missing_ok=True)

    def test_apply_is_idempotent_and_uses_snapshots_without_payments_or_stock(self):
        path = self._csv()
        try:
            call_command('import_pre_system_sales', '--file', str(path), '--apply')
            self.assertEqual(Order.objects.count(), 1)
            self.assertEqual(OrderItem.objects.count(), 1)
            self.assertEqual(Payment.objects.count(), 0)

            order = Order.objects.get()
            item = OrderItem.objects.get()
            self.assertEqual(order.status, Order.Status.SERVED)
            self.assertEqual(order.created_at.year, 2026)
            self.assertEqual(order.created_at.month, 7)
            self.assertEqual(item.quantity, 17)
            self.assertEqual(item.product_name_ar_snapshot, 'قهوة')
            self.assertEqual(item.unit_price_syp_snapshot, 100)
            self.assertEqual(item.line_total_syp_snapshot, 1700)
            self.assertEqual(item.prep_status, OrderItem.PrepStatus.NO_PREP)
            self.assertFalse(item.stock_deducted)
            self.assertIsNone(item.estimated_unit_cost_syp_snapshot)
            self.assertIn('needs_review', item.item_note)

            call_command('import_pre_system_sales', '--file', str(path), '--apply')
            self.assertEqual(Order.objects.count(), 1)
            self.assertEqual(OrderItem.objects.count(), 1)
            self.assertEqual(Payment.objects.count(), 0)
        finally:
            path.unlink(missing_ok=True)
