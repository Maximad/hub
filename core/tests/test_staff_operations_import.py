import io
import os
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.formats import localize

from core.management.commands.generate_hub_operations_template import workbook_bytes
from core.management.commands.import_hub_operations_batch import ORDER, SHEETS
from core.models import InventoryItem, Payment, Purchase, StockMovement
from core.services.operations_import import Plan
from vendors.models import Vendor


@override_settings(SECURE_SSL_REDIRECT=False)
class StaffOperationsImportTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_user(username='operations-admin', password='pass', phone='+96399001', role='admin')
        self.cashier = users.create_user(username='operations-cashier', password='pass', phone='+96399002', role='cashier')

    def test_non_admin_cannot_access_upload(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse('staff_operations_import_upload'))
        self.assertRedirects(response, reverse('staff_home'))

    def test_admin_can_access_upload_and_template(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('staff_operations_import_upload')), 'استيراد العمليات والمخزون')
        response = self.client.get(reverse('staff_operations_import_template'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('hub_operations_template.xlsx', response['Content-Disposition'])
        with ZipFile(io.BytesIO(response.content)) as archive:
            workbook = archive.read('xl/workbook.xml').decode()
        for section in ORDER:
            self.assertIn(SHEETS[section], workbook)

    def test_untouched_template_previews_without_errors(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile('operations.xlsx', workbook_bytes(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response = self.client.post(reverse('staff_operations_import_preview'), {'xlsx_file': upload})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_errors'])
        self.assertFalse(response.context['has_warnings'])
        self.assertContains(response, 'تأثير المخزون المتوقع')
        self.assertContains(response, 'تأثير الدفعات المتوقع')
        self.assertContains(response, 'لا يوجد', count=2)
        self.assertNotIn('items', response.context['plan'].stock)
        self.assertNotIn('items', response.context['plan'].payments)

    def test_stock_effect_preview_renders_and_makes_no_permanent_writes(self):
        self.client.force_login(self.admin)
        item = InventoryItem.objects.create(code='ING-HOTFIX', name_ar='بن الاختبار', unit=InventoryItem.Unit.KG)
        vendor = Vendor.objects.create(name_ar='مورد الاختبار')
        rows = {
            'purchases': ['CREATE_AND_RECEIVE', 'مراجع', 'PUR-HOTFIX-001', '2026-01-15', '', vendor.name_ar, 'INV-001', '0', ''],
            'purchase_items': ['CREATE', 'LINE-HOTFIX-001', 'PUR-HOTFIX-001', item.code, item.name_ar, '2.5', 'kg', '10000', ''],
        }
        with patch.dict('core.management.commands.generate_hub_operations_template.EXAMPLES', rows):
            upload = SimpleUploadedFile('stock.xlsx', workbook_bytes(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        before = (Purchase.objects.count(), StockMovement.objects.count(), item.current_quantity)

        response = self.client.post(reverse('staff_operations_import_preview'), {'xlsx_file': upload})

        item.refresh_from_db()
        self.assertEqual((Purchase.objects.count(), StockMovement.objects.count(), item.current_quantity), before)
        self.assertNotIn('items', response.context['plan'].stock)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_errors'])
        self.assertContains(response, 'ING-HOTFIX')
        self.assertContains(response, f'{localize(item._meta.get_field("current_quantity").to_python("2.500"))} kg')

    def test_payment_effect_preview_renders_without_mutating_defaultdict(self):
        self.client.force_login(self.admin)
        plan = Plan()
        plan.payments['cash'] = 12500
        upload = SimpleUploadedFile('payments.xlsx', workbook_bytes(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        with patch('core.views.staff_import.OperationsImportEngine.preview', return_value=plan):
            response = self.client.post(reverse('staff_operations_import_preview'), {'xlsx_file': upload})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'نقداً: 12500')
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(dict(plan.payments), {'cash': 12500})

    def test_rendering_failure_removes_temporary_upload(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile('operations.xlsx', workbook_bytes(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        with tempfile.TemporaryDirectory() as directory:
            with patch('core.views.staff_import.OPERATIONS_TEMP_DIR', directory), patch(
                'core.views.staff_import.render', side_effect=RuntimeError('render failed')
            ):
                with self.assertRaisesMessage(RuntimeError, 'render failed'):
                    self.client.post(reverse('staff_operations_import_preview'), {'xlsx_file': upload})
            self.assertEqual(os.listdir(directory), [])

        self.assertNotIn('bulk_import_operations_upload', self.client.session)

    def test_invalid_extension_and_oversize_are_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('staff_operations_import_preview'), {'xlsx_file': SimpleUploadedFile('bad.csv', b'x')}, follow=True)
        self.assertContains(response, 'امتداد .xlsx')
        response = self.client.post(reverse('staff_operations_import_preview'), {'xlsx_file': SimpleUploadedFile('huge.xlsx', b'x' * (5 * 1024 * 1024 + 1))}, follow=True)
        self.assertContains(response, '5 MB')

    def test_missing_temp_file_has_friendly_message(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['bulk_import_operations_upload'] = {'token': '11111111-1111-1111-1111-111111111111', 'signature': {}}
        session.save()
        response = self.client.post(reverse('staff_operations_import_confirm'), follow=True)
        self.assertContains(response, 'انتهت جلسة المعاينة أو لم يعد الملف متاحاً')
