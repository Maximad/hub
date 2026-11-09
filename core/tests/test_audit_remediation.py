from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from core.finance import current_business_date
from core.settings_helpers import get_system_settings
from core.models import (Category, Expense, ExpenseCategory, FinancialAccount, Order,
                         OrderItem, Payment, Product, Purchase, Room, SystemSetting, TableArea, Transfer)
from core.services.posting.context import PostingContext
from core.services.posting.exceptions import IdempotencyConflict, InvalidTransition
from core.services.posting.order_payments import collect, _collection_account
from core.services.table_visit_access import create_table_visit, verify_table_pin, visit_join_pin
from core.views.staff_finance import _authenticated_approver
from locations.models import TablePINThrottle


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
class AuditAccessTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.actor = users.create_user(username='audit-finance', phone='+901', password='actor', role='admin')
        self.manager = users.create_user(username='audit-approver', phone='+902', password='manager-secret', role='admin')
        self.room = Room.objects.create(name_ar='Audit room')
        self.table = TableArea.objects.create(room=self.room, name_ar='Audit table')

    def test_selected_manager_id_without_password_is_not_approval(self):
        request = RequestFactory().post('/', {'approver': self.manager.pk})
        request.user = self.actor
        with self.assertRaises(ValidationError):
            _authenticated_approver(request)
        request.POST = request.POST.copy()
        request.POST['approver_password'] = 'wrong'
        with self.assertRaises(ValidationError):
            _authenticated_approver(request)
        request.POST['approver_password'] = 'manager-secret'
        self.assertEqual(_authenticated_approver(request), self.manager)
        request.user = self.manager
        with self.assertRaises(ValidationError):
            _authenticated_approver(request)

    def test_transfer_http_requires_actual_manager_authentication(self):
        source = FinancialAccount.objects.create(code='audit:source', name_ar='source', account_type='asset', is_active=True)
        destination = FinancialAccount.objects.create(code='audit:dest', name_ar='dest', account_type='asset', is_active=True)
        self.client.force_login(self.actor)
        payload = {'source_account': source.pk, 'destination_account': destination.pk,
                   'amount': '50000', 'reason': 'Approved transfer', 'business_date': '2026-11-09',
                   'approver': self.manager.pk, 'currency_acknowledged': 'on'}
        url = reverse('staff_finance_transfer_new')
        self.client.post(url, payload)
        self.assertFalse(Transfer.objects.exists())
        payload['approver_password'] = 'manager-secret'
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Transfer.objects.get().approver_id, self.manager.pk)

    def test_new_client_cannot_reset_table_lock_and_expiry_recovers(self):
        SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)
        visit = create_table_visit(self.table)
        correct = visit_join_pin(visit)
        wrong = '0000' if correct != '0000' else '0001'
        url = reverse('menu_table', args=[self.table.qr_token])
        for _ in range(5):
            Client().post(url, {'visit_action': 'join', 'pin': wrong})
        bucket = TablePINThrottle.objects.get(table=self.table)
        self.assertEqual(bucket.failures, 5)
        fresh = Client()
        response = fresh.post(url, {'visit_action': 'join', 'pin': correct})
        self.assertNotIn('hub_visit', response.cookies)
        with self.assertRaises(ValidationError):
            verify_table_pin(self.table, correct)
        future = timezone.now() + timedelta(minutes=6)
        with patch('core.services.table_visit_access.timezone.now', return_value=future):
            self.assertEqual(verify_table_pin(self.table, correct), visit)

    def test_receipt_files_and_thumbnails_are_private_but_product_media_is_public(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            asset = MediaAsset.objects.create(title_ar='Receipt', file=SimpleUploadedFile('receipt.pdf', b'%PDF-1.4\n', content_type='application/pdf'), media_type='document')
            public = MediaAsset.objects.create(title_ar='Public', file=SimpleUploadedFile('public.pdf', b'public'), media_type='document')
            Purchase.objects.create(business_date='2026-11-09', supplier_name='supplier', receipt_media=asset)
            url = '/media/' + asset.file.name
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get('/media/' + public.file.name).status_code, 200)
            self.assertEqual(self.client.get('/media/./' + asset.file.name).status_code, 404)
            self.client.force_login(self.actor)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.client.logout()
            image = MediaAsset.objects.create(title_ar='Receipt image', file='products/receipt.jpg')
            thumbnail = Path(directory) / 'products/receipt-card-320w.webp'
            thumbnail.parent.mkdir(parents=True, exist_ok=True)
            thumbnail.write_bytes(b'thumbnail')
            category = ExpenseCategory.objects.create(code='audit', name_ar='Audit')
            Expense.objects.create(business_date='2026-11-09', category=category, title='receipt', amount_syp=1, receipt_media=image)
            self.assertEqual(self.client.get('/media/products/receipt-card-320w.webp').status_code, 404)


@override_settings(COLLECTION_ACCOUNT_CODES={}, COLLECTION_BUSINESS_UNIT='audit-tests')
class AuditPaymentTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='audit-cashier', password='x', phone='+903', role='admin')
        self.cash = FinancialAccount.objects.create(code='audit:cash', name_ar='cash', account_type='asset', scope='cashbox', business_unit='audit-tests', is_active=True)
        self.revenue = FinancialAccount.objects.create(code='audit:revenue', name_ar='revenue', account_type='revenue', business_unit='audit-tests', is_active=True)
        self.category = Category.objects.create(name_ar='Audit')
        self.product = Product.objects.create(category=self.category, name_ar='Item', price_syp=100)

    def order(self):
        order = Order.objects.create()
        OrderItem.objects.create(order=order, product=self.product, quantity=1,
                                 product_name_ar_snapshot='Item', unit_price_syp_snapshot=100, line_total_syp_snapshot=100)
        return order

    def test_replay_rejects_changed_amount_method_or_order(self):
        order = self.order()
        context = PostingContext(actor=self.actor, business_date=datetime(2026, 11, 9).date(), idempotency_key='audit-retry')
        first = collect(order, context, 40, Payment.Method.CASH)
        self.assertEqual(collect(order, context, 40, Payment.Method.CASH), first)
        for target, amount, method in [(order, 50, 'cash'), (order, 40, 'manual_transfer'), (self.order(), 40, 'cash')]:
            with self.assertRaises(IdempotencyConflict):
                collect(target, context, amount, method)
        self.assertEqual(Payment.objects.count(), 1)

    def test_foreign_currency_and_other_unit_accounts_are_never_selected(self):
        FinancialAccount.objects.create(code='aaa:usd', name_ar='USD', account_type='asset', scope='cashbox', currency='USD', is_active=True)
        FinancialAccount.objects.create(code='aaa:other', name_ar='Other', account_type='asset', scope='cashbox', business_unit='other', is_active=True)
        self.assertEqual(_collection_account('cash'), self.cash)
        extra = FinancialAccount.objects.create(code='audit:second', name_ar='second', account_type='asset', scope='cashbox', business_unit='audit-tests', is_active=True)
        with self.assertRaises(InvalidTransition):
            _collection_account('cash')
        with override_settings(COLLECTION_ACCOUNT_CODES={'cash': extra.code}):
            self.assertEqual(_collection_account('cash'), extra)
        with override_settings(COLLECTION_ACCOUNT_CODES={'cash': 'aaa:usd'}):
            with self.assertRaises(InvalidTransition):
                _collection_account('cash')

    @override_settings(BUSINESS_DAY_CUTOFF_HOUR=4, TIME_ZONE='Asia/Damascus')
    def test_payment_context_and_finance_share_the_overnight_business_date(self):
        local_tz = datetime_timezone(timedelta(hours=3))
        for hour, minute, expected in [(23, 59, 9), (0, 1, 8), (3, 59, 8), (4, 0, 9)]:
            at = datetime(2026, 11, 9, hour, minute, tzinfo=local_tz)
            with patch('django.utils.timezone.now', return_value=at):
                self.assertEqual(current_business_date().day, expected)
                self.assertEqual(PostingContext().date_for(), current_business_date())
