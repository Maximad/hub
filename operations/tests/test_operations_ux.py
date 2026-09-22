from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import BusinessDayChecklistItem, BusinessDayTask
from operations.services import open_business_day
from operations.tasks import create_manual_task


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    BUSINESS_DAY_CUTOFF_HOUR=4,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class OperationsUxTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='ops-ux-admin',
            password='pass',
            email='ops-ux@example.com',
            phone='+963955300001',
        )
        self.waiter = User.objects.create_user(
            username='ops-ux-waiter',
            first_name='رامي',
            last_name='الخطيب',
            password='pass',
            phone='+963955300002',
            role=User.Role.WAITER,
        )
        self.day = open_business_day(
            actor=self.admin,
            business_date=date(2026, 9, 22),
        )
        self.client.force_login(self.admin)

    def test_task_page_uses_direct_state_actions_not_status_select(self):
        task = create_manual_task(
            self.day,
            actor=self.admin,
            title='تجهيز الطاولات',
            assigned_to=self.waiter,
        )

        response = self.client.get(reverse('staff_operational_tasks'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '✓ تم')
        self.assertContains(response, 'تعذّر')
        self.assertNotContains(response, '<select name="status"', html=False)
        self.assertContains(response, 'الروتينات الدورية والإعدادات')
        self.assertContains(response, 'رامي الخطيب')

        done = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'status',
            'task_id': task.pk,
            'status': 'done',
        })
        self.assertEqual(done.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.DONE)

    def test_task_waiver_direct_action_still_requires_reason(self):
        task = create_manual_task(self.day, actor=self.admin, title='مهمة قابلة للتعذر')

        response = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'status',
            'task_id': task.pk,
            'status': 'waived',
            'completion_note': '',
        }, follow=True)

        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)
        self.assertContains(response, 'سبب إغلاق المهمة دون تنفيذ مطلوب')

    def test_business_day_opening_checklist_uses_direct_buttons(self):
        item = self.day.checklist_items.filter(
            status=BusinessDayChecklistItem.Status.PENDING,
        ).exclude(code='staff_roster').first()
        self.assertIsNotNone(item)

        response = self.client.get(reverse('staff_close_day'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '✓ جاهز')
        self.assertContains(response, 'تجاوز بسبب')
        self.assertNotContains(response, '<select name="checklist_status"', html=False)
        self.assertNotContains(response, 'check-in')
        self.assertContains(response, 'تأكيداً تشغيلياً')

        update = self.client.post(reverse('staff_close_day'), {
            'business_day_action': 'checklist',
            'checklist_item_id': item.pk,
            'checklist_status': 'done',
        })
        self.assertEqual(update.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, BusinessDayChecklistItem.Status.DONE)

    def test_business_day_exposes_command_center_navigation(self):
        response = self.client.get(reverse('staff_close_day'))

        self.assertContains(response, 'العمليات')
        self.assertContains(response, 'المهام')
        self.assertContains(response, 'الافتتاح')
        self.assertContains(response, 'فريق اليوم')
        self.assertContains(response, 'التسليم')
        self.assertContains(response, 'الإغلاق')
        self.assertContains(response, 'السجل والتدقيق')

    def test_staff_shell_promotes_day_and_tasks(self):
        response = self.client.get(reverse('staff_operational_tasks'))

        self.assertContains(response, '>اليوم</a>', html=False)
        self.assertContains(response, '>المهام</a>', html=False)
