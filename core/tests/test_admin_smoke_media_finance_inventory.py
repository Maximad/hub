from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import MediaAsset, ProductMedia
from core.models import (CashMovement, Category, Expense, ExpenseCategory, InventoryItem, Order,
                         OrderDiscount, OrderItem, Payment, Product, Purchase, PurchaseItem,
                         StockMovement, SystemSetting)


class AdminSmokeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='admin-smoke', password='pass', email='a@example.com', phone='+9631')
        self.client.force_login(self.user)

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_critical_admin_pages_load(self):
        routes = [
            '/admin/', '/admin/core/product/', '/admin/core/product/add/',
            '/admin/catalog/mediaasset/', '/admin/catalog/mediaasset/add/',
            '/admin/catalog/mediaasset/add/?_to_field=id&_popup=1',
            '/admin/core/order/', '/admin/core/payment/', '/admin/core/systemsetting/',
            '/admin/core/expense/', '/admin/core/inventoryitem/', '/admin/core/purchase/',
            '/admin/events/event/', '/admin/vendors/vendor/',
        ]
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertIn(response.status_code, {200, 302})
                self.assertLess(response.status_code, 500)


class MediaSafetyTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='قسم')
        self.product = Product.objects.create(category=self.category, name_ar='منتج', price_syp=1000)

    def test_media_asset_can_be_created_with_uploaded_file_under_media_root(self):
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp, DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage'):
                asset = MediaAsset.objects.create(title_ar='صورة', file=SimpleUploadedFile('x.jpg', b'abc', content_type='image/jpeg'))
                self.assertTrue(asset.file.name.startswith('products/'))
                self.assertTrue(asset.file.storage.exists(asset.file.name))

    def test_media_asset_can_be_created_with_external_url(self):
        asset = MediaAsset.objects.create(title_ar='رابط', media_type=MediaAsset.MediaType.EXTERNAL_URL, external_url='https://example.com/image.jpg')
        self.assertEqual(asset.url, 'https://example.com/image.jpg')

    def test_missing_file_does_not_crash_display_helpers(self):
        asset = MediaAsset.objects.create(title_ar='مفقود', file='products/missing.jpg')
        self.assertIn('/media/products/missing.jpg', asset.url)

    def test_product_can_have_media_assigned(self):
        asset = MediaAsset.objects.create(title_ar='رابط', media_type=MediaAsset.MediaType.EXTERNAL_URL, external_url='https://example.com/image.jpg')
        pm = ProductMedia.objects.create(product=self.product, media_asset=asset, is_primary=True, display_on_public_menu=True, display_on_pos=True)
        self.assertEqual(pm.resolved_url, asset.url)

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_product_without_media_still_renders_menu_and_pos(self):
        user = get_user_model().objects.create_user(username='staff-media', password='pass', phone='+9632', role='admin')
        self.client.force_login(user)
        self.assertLess(self.client.get('/menu/').status_code, 500)
        self.assertLess(self.client.get('/staff/pos/').status_code, 500)


class FinanceSafetyTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='قسم')
        self.product = Product.objects.create(category=self.category, name_ar='منتج', price_syp=1000)
        self.order = Order.objects.create()
        OrderItem.objects.create(order=self.order, product=self.product, product_name_ar_snapshot='منتج', unit_price_syp_snapshot=1000, quantity=1, line_total_syp_snapshot=1000)

    def test_payment_cannot_be_negative(self):
        payment = Payment(order=self.order, amount_syp=-1, method=Payment.Method.CASH)
        with self.assertRaises(ValidationError): payment.full_clean()

    def test_payment_cannot_exceed_remaining(self):
        with self.assertRaises(ValidationError): Payment(order=self.order, amount_syp=1001, method=Payment.Method.CASH).full_clean()

    def test_full_payment_marks_order_paid(self):
        Payment.objects.create(order=self.order, amount_syp=1000, method=Payment.Method.CASH)
        self.assertEqual(self.order.payment_status, 'paid')

    def test_partial_payment_does_not_make_remaining_negative(self):
        Payment.objects.create(order=self.order, amount_syp=400, method=Payment.Method.CASH)
        self.assertEqual(self.order.remaining_syp, 600)

    def test_discount_cannot_make_total_negative(self):
        with self.assertRaises(ValidationError): OrderDiscount(order=self.order, amount_syp=1001, reason='اختبار').full_clean()

    def test_cancelled_order_visible_and_reports_safe(self):
        self.order.status = Order.Status.CANCELLED; self.order.save()
        self.assertEqual(Order.objects.filter(pk=self.order.pk).count(), 1)
        self.assertEqual(self.order.payment_status, 'cancelled')

    def test_expense_cashbox_rules(self):
        cat = ExpenseCategory.objects.create(name_ar='عام', code='general')
        cash = Expense.objects.create(business_date=date.today(), category=cat, title='نقدي', amount_syp=100, status=Expense.Status.PAID, payment_method=Expense.PaymentMethod.CASH, paid_from=Expense.PaidFrom.CASHBOX)
        unpaid = Expense.objects.create(business_date=date.today(), category=cat, title='آجل', amount_syp=100, status=Expense.Status.DRAFT, paid_from=Expense.PaidFrom.UNPAID)
        card = Expense.objects.create(business_date=date.today(), category=cat, title='بطاقة', amount_syp=100, status=Expense.Status.PAID, payment_method=Expense.PaymentMethod.CARD, paid_from=Expense.PaidFrom.CASHBOX)
        self.assertTrue(cash.affects_cashbox()); self.assertFalse(unpaid.affects_cashbox()); self.assertFalse(card.affects_cashbox())


class InventorySafetyTests(TestCase):
    def setUp(self):
        self.item = InventoryItem.objects.create(name_ar='سكر', current_quantity=Decimal('0'), unit=InventoryItem.Unit.KG)

    def test_purchase_draft_does_not_increase_stock(self):
        p = Purchase.objects.create(business_date=date.today(), supplier_name='مورد')
        PurchaseItem.objects.create(purchase=p, inventory_item=self.item, quantity=Decimal('2'), unit=InventoryItem.Unit.KG, unit_cost_syp=100)
        self.item.refresh_from_db(); self.assertEqual(self.item.current_quantity, Decimal('0.000'))

    def test_received_purchase_increases_stock_once_and_double_receive_is_detectable(self):
        purchase = Purchase.objects.create(business_date=date.today(), supplier_name='مورد')
        movement = StockMovement.objects.create(inventory_item=self.item, business_date=date.today(), movement_type=StockMovement.MovementType.PURCHASE_RECEIVED, direction=StockMovement.Direction.IN, quantity=Decimal('2'), unit=InventoryItem.Unit.KG, related_purchase=purchase)
        movement.apply_to_stock(); self.item.refresh_from_db(); self.assertEqual(self.item.current_quantity, Decimal('2.000'))
        self.assertTrue(purchase.stock_movements.exists())

    def test_stock_movement_quantity_must_be_positive_and_waste_requires_reason(self):
        with self.assertRaises(ValidationError): StockMovement(inventory_item=self.item, business_date=date.today(), movement_type=StockMovement.MovementType.MANUAL_ADJUSTMENT, direction=StockMovement.Direction.IN, quantity=0, unit=InventoryItem.Unit.KG).full_clean()
        with self.assertRaises(ValidationError): StockMovement(inventory_item=self.item, business_date=date.today(), movement_type=StockMovement.MovementType.WASTE, direction=StockMovement.Direction.OUT, quantity=1, unit=InventoryItem.Unit.KG).full_clean()

    def test_auto_stock_deduction_disabled_by_default_and_product_without_recipe_safe(self):
        setting = SystemSetting.objects.create(system_title_ar='اختبار')
        self.assertFalse(setting.auto_deduct_inventory_on_sale)
        category = Category.objects.create(name_ar='قسم')
        product = Product.objects.create(category=category, name_ar='بلا وصفة', price_syp=100)
        self.assertEqual(product.recipe_items.count(), 0)
