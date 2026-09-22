from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import NotificationRecipient
from operations.models import BusinessDayTask, StaffDailyCodeReceipt
from operations.opening import set_staff_roster
from operations.services import issue_daily_code, open_business_day
from operations.tasks import create_manual_task, set_task_status


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
        self.extra_staff = User.objects.create_user(
            username='ops-ux-extra', password='pass', phone='+963955299902',
            role=User.Role.WAITER,
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

    def test_daily_code_delivery_and_receipts_follow_final_roster(self):
        self.assertTrue(StaffDailyCodeReceipt.objects.filter(
            business_day=self.day, user=self.extra_staff,
        ).exists())

        with self.captureOnCommitCallbacks(execute=True):
            set_staff_roster(
                self.day,
                actor=self.admin,
                user_ids=[str(self.admin.pk)],
            )

        self.assertEqual(
            set(StaffDailyCodeReceipt.objects.filter(business_day=self.day).values_list('user_id', flat=True)),
            {self.admin.pk},
        )
        self.assertFalse(NotificationRecipient.objects.filter(
            notification_event__title_ar=f'رمز الفريق ليوم {self.day.business_date} جاهز',
            user=self.extra_staff,
        ).exists())

        with self.captureOnCommitCallbacks(execute=True):
            issue_daily_code(self.day, actor=self.admin)

        self.assertEqual(
            set(StaffDailyCodeReceipt.objects.filter(business_day=self.day).values_list('user_id', flat=True)),
            {self.admin.pk},
        )
