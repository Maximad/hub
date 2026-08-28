from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Room, SystemSetting, TableArea
from core.services.table_visit_access import resolve_table_number
from core.settings_helpers import get_system_settings
from locations.models import TableAreaSettings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class TableAreaSettingsTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='مدور 2')
        self.table_settings = TableAreaSettings.objects.create(
            table=self.table,
            customer_entry_code='11',
            staff_description='الطاولة المدورة بجانب البار — وصف داخلي فقط',
        )
        SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_explicit_number_resolves_independently_of_table_name(self):
        self.assertEqual(resolve_table_number('11'), self.table)

        self.table.name_ar = 'الطاولة قرب الشباك'
        self.table.save(update_fields=('name_ar', 'updated_at'))

        self.assertEqual(resolve_table_number('11'), self.table)

    def test_arabic_digits_are_normalized_for_storage_and_lookup(self):
        self.table_settings.customer_entry_code = '٠١١'
        self.table_settings.save()
        self.table_settings.refresh_from_db()

        self.assertEqual(self.table_settings.customer_entry_code, '11')
        self.assertEqual(resolve_table_number('١١'), self.table)

    def test_digits_in_display_name_are_not_customer_entry_codes(self):
        other = TableArea.objects.create(room=self.room, name_ar='طاولة 99')

        with self.assertRaisesMessage(ValidationError, 'رقم الطاولة غير موجود.'):
            resolve_table_number('99')

        self.assertFalse(TableAreaSettings.objects.filter(table=other).exists())

    def test_customer_entry_code_is_unique(self):
        other = TableArea.objects.create(room=self.room, name_ar='مربع خشب')

        with self.assertRaises(ValidationError):
            TableAreaSettings.objects.create(table=other, customer_entry_code='11')

    def test_staff_description_is_not_rendered_on_public_table_entry(self):
        response = self.client.get(
            reverse('menu_table', kwargs={'qr_token': self.table.qr_token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.table_settings.staff_description)

    def test_table_admin_edits_name_number_and_staff_description_together(self):
        admin_user = get_user_model().objects.create_superuser(
            username='table-settings-admin',
            password='pass',
            email='tables@example.com',
            phone='+963900001122',
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse('admin:core_tablearea_change', args=(self.table.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'رقم الطاولة للزبون')
        self.assertContains(response, '11')
        self.assertContains(response, self.table_settings.staff_description)
        self.assertContains(response, self.table.name_ar)

    def test_changing_entry_number_does_not_change_qr_token(self):
        original_qr = self.table.qr_token
        self.table_settings.customer_entry_code = '27'
        self.table_settings.save()
        self.table.refresh_from_db()

        self.assertEqual(self.table.qr_token, original_qr)
        self.assertEqual(resolve_table_number('27'), self.table)
        with self.assertRaisesMessage(ValidationError, 'رقم الطاولة غير موجود.'):
            resolve_table_number('11')
