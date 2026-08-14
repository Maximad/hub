import io
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.management.commands.generate_hub_operations_template import workbook_bytes
from core.management.commands.import_hub_operations_batch import ORDER, SHEETS


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
        self.assertContains(response, 'بنود المشتريات')

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
