"""Arabic staff reminder UI: authorization, validation and source ownership."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.duty import (
    accept_duty_handover, assign_duty_coverage, request_duty_handover,
)
from operations.models import BusinessDayTask, Reminder
from operations.opening import set_staff_roster
from operations.reminders import create_standalone_reminder, sync_task_reminders
from operations.services import open_business_day
from operations.tasks import create_manual_task


@override_settings(REMINDERS_ENGINE_ENABLED=True, DUTY_NOTIFICATION_ROUTING_ENABLED=False)
class ReminderStaffUITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username='ui-reminder-owner', phone='+963955983101',
            email='ui-reminders@example.org', password='pass',
        )
        self.waiter_a = User.objects.create_user(
            username='ui-reminder-a', phone='+963955983102',
            role=User.Role.WAITER, password='pass',
        )
        self.waiter_b = User.objects.create_user(
            username='ui-reminder-b', phone='+963955983103',
            role=User.Role.WAITER, password='pass',
        )
        self.provider = User.objects.create_user(
            username='ui-reminder-provider', phone='+963955983104',
            role=User.Role.INTERNET_PROVIDER, password='pass',
        )
        self.day = open_business_day(actor=self.owner)
        set_staff_roster(
            self.day, actor=self.owner,
            user_ids=[self.owner.pk, self.waiter_a.pk, self.waiter_b.pk],
        )
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        assign_duty_coverage(
            self.day, responsibility='service',
            user_id=self.waiter_a.pk, actor=self.owner,
        )
        self.url = reverse('staff_reminders')

    def _reminder(self, *, assigned_to=None, responsibility='service'):
        return create_standalone_reminder(
            actor=self.owner,
            title='تفقّد الطاولات',
            due_at=timezone.now() + timedelta(hours=1),
            assigned_to=assigned_to,
            responsibility=responsibility,
        )

    def test_staff_requires_login_and_excludes_provider(self):
        self.assertIn(self.client.get(self.url).status_code, (302, 403))
        self.client.force_login(self.provider)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'reminder_action': 'create'}).status_code, 403)

    def test_manager_can_create_once_reminder_from_arabic_form(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url), 'تذكير جديد')
        due = timezone.localtime(timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(self.url, {
            'reminder_action': 'create',
            'title_ar': 'تجهيز القاعة',
            'details': 'فحص الأجهزة',
            'due_at': due,
            'repeat': 'once',
            'interval_minutes': '',
            'responsibility': 'service',
            'assigned_to': str(self.waiter_a.pk),
            'expires_at': '',
        })
        self.assertRedirects(response, self.url)
        reminder = Reminder.objects.get(title_ar='تجهيز القاعة')
        self.assertEqual(reminder.created_by, self.owner)
        self.assertEqual(reminder.assigned_to, self.waiter_a)
        self.assertEqual(reminder.repeat, Reminder.Repeat.ONCE)
        self.assertEqual(reminder.responsibility, 'service')

    def test_invalid_repeat_does_not_create_reminder(self):
        self.client.force_login(self.owner)
        due = timezone.localtime(timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(self.url, {
            'reminder_action': 'create', 'title_ar': 'اختبار خطأ',
            'due_at': due, 'repeat': 'interval', 'interval_minutes': '0',
            'responsibility': 'service',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تذكير جديد')
        self.assertFalse(Reminder.objects.filter(title_ar='اختبار خطأ').exists())

    def test_non_manager_cannot_create_and_only_sees_current_duty(self):
        rem = self._reminder(assigned_to=self.waiter_a)
        self.client.force_login(self.waiter_b)
        self.assertNotContains(self.client.get(self.url), rem.title_ar)
        self.assertEqual(
            self.client.post(self.url, {'reminder_action': 'create'}).status_code, 403
        )
        self.client.force_login(self.waiter_a)
        self.assertContains(self.client.get(self.url), rem.title_ar)

    def test_on_duty_holder_can_complete_and_snooze_standalone(self):
        rem = self._reminder(assigned_to=self.waiter_a)
        self.client.force_login(self.waiter_a)
        response = self.client.post(self.url, {
            'reminder_action': 'snooze',
            'reminder_id': str(rem.pk), 'snooze_minutes': '15',
        })
        self.assertRedirects(response, self.url)
        rem.refresh_from_db()
        self.assertGreater(rem.next_due_at, timezone.now() + timedelta(minutes=14))
        response = self.client.post(self.url, {
            'reminder_action': 'complete', 'reminder_id': str(rem.pk),
        })
        self.assertRedirects(response, self.url)
        rem.refresh_from_db()
        self.assertEqual(rem.status, Reminder.Status.COMPLETED)
        self.assertIsNone(rem.next_due_at)

    def test_invalid_snooze_and_fake_reminder_id_are_safe(self):
        rem = self._reminder(assigned_to=self.waiter_a)
        self.client.force_login(self.waiter_a)
        response = self.client.post(self.url, {
            'reminder_action': 'snooze',
            'reminder_id': str(rem.pk), 'snooze_minutes': '9999',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'مدة التأجيل غير صالحة')
        self.assertEqual(
            self.client.post(self.url, {
                'reminder_action': 'complete', 'reminder_id': 'bad',
            }).status_code, 404
        )
        rem.refresh_from_db()
        self.assertEqual(rem.status, Reminder.Status.ACTIVE)

    def test_accepted_handover_removes_old_staff_access(self):
        rem = self._reminder(assigned_to=self.waiter_a)
        request_duty_handover(
            self.day, responsibility='service',
            to_user_id=self.waiter_b.pk, actor=self.waiter_a,
        )
        accept_duty_handover(
            self.day, responsibility='service', actor=self.waiter_b,
        )
        self.client.force_login(self.waiter_a)
        self.assertNotContains(self.client.get(self.url), rem.title_ar)
        self.assertEqual(self.client.post(self.url, {
            'reminder_action': 'complete', 'reminder_id': str(rem.pk),
        }).status_code, 403)
        self.client.force_login(self.waiter_b)
        self.assertContains(self.client.get(self.url), rem.title_ar)
        self.assertRedirects(self.client.post(self.url, {
            'reminder_action': 'complete', 'reminder_id': str(rem.pk),
        }), self.url)

    def test_source_task_reminder_can_only_be_closed_from_task_workspace(self):
        task = create_manual_task(
            self.day, actor=self.owner, title='مهمة أصلية',
            due_at=timezone.now() + timedelta(minutes=15),
            assigned_to=self.waiter_a,
        )
        self.assertEqual(sync_task_reminders(self.day), 1)
        rem = task.reminder_schedule
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url), 'فتح المهمة')
        response = self.client.post(self.url, {
            'reminder_action': 'complete', 'reminder_id': str(rem.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'أغلق المهمة الأصلية')
        rem.refresh_from_db()
        self.assertEqual(rem.status, Reminder.Status.ACTIVE)
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)

    def test_manager_can_cancel_and_history_displays_record(self):
        rem = self._reminder()
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(self.url, {
            'reminder_action': 'cancel', 'reminder_id': str(rem.pk),
        }), self.url)
        response = self.client.get(self.url+'?view=history')
        self.assertContains(response, rem.title_ar)
        rem.refresh_from_db()
        self.assertEqual(rem.status, Reminder.Status.CANCELLED)

    def test_disabled_flag_allows_read_only_notice_but_not_mutation(self):
        self.client.force_login(self.owner)
        with override_settings(REMINDERS_ENGINE_ENABLED=False):
            self.assertContains(
                self.client.get(self.url), 'التذكيرات غير مفعّلة بعد'
            )
            self.assertEqual(self.client.post(self.url, {
                'reminder_action': 'create',
            }).status_code, 403)

    def test_tasks_navigation_respects_opt_in_setting(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('staff_operational_tasks'))
        self.assertContains(response, self.url)
        with override_settings(REMINDERS_ENGINE_ENABLED=False):
            response = self.client.get(reverse('staff_operational_tasks'))
            self.assertNotContains(response, self.url)
