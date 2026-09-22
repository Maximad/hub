from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from operations.services import open_business_day
from operations.tasks import create_manual_task, set_task_status
from operations.models import BusinessDayTask


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_ENABLED=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class OperationsUxTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='ops-ux-admin', password='pass',
            email='ops-ux@example.com', phone='+963955299901',
        )
        self.day = open_business_day(
            actor=self.admin,
            business_date=date(2026, 9, 22),
        )
        self.client.force_login(self.admin)

    def test_daily_tasks_use_direct_actions_not_status_dropdown(self):
        create_manual_task(self.day, actor=self.admin, title='فحص الطاولات')
        response = self.client.get(reverse('staff_operational_tasks'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'مهام اليوم')
        self.assertContains(response, '✓ تمت')
        self.assertContains(response, 'تعذّر التنفيذ')
        self.assertNotContains(response, '<select name="status">', html=False)
        self.assertContains(response, '+ إضافة مهمة اليوم')
        self.assertContains(response, 'الروتينات الدورية')

    def test_completed_task_offers_reopen_action(self):
        task = create_manual_task(self.day, actor=self.admin, title='إغلاق البار')
        set_task_status(task, actor=self.admin, status=BusinessDayTask.Status.DONE)
        response = self.client.get(reverse('staff_operational_tasks'))
        self.assertContains(response, 'إعادة فتح')

    def test_shell_navigation_exposes_business_day_and_tasks_on_mobile_and_desktop(self):
        response = self.client.get(reverse('staff_operational_tasks'))
        content = response.content.decode('utf-8')
        self.assertGreaterEqual(content.count(reverse('staff_close_day')), 2)
        self.assertGreaterEqual(content.count(reverse('staff_operational_tasks')), 2)
        self.assertIn('يوم العمل', content)
        self.assertIn('مهام اليوم', content)

    def test_operations_polish_assets_are_loaded(self):
        response = self.client.get(reverse('staff_close_day'))
        self.assertContains(response, 'css/operations_ux.css')
        self.assertContains(response, 'js/operations_ux.js')
