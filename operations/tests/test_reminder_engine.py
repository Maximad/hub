"""Opt-in independent reminder engine contracts and existing task compatibility."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import NotificationEvent, NotificationLog, NotificationRecipient
from operations.duty import (
    accept_duty_handover, assign_duty_coverage, request_duty_handover,
)
from operations.models import BusinessDayTask, Reminder, ReminderOccurrence
from operations.opening import set_staff_roster
from operations.reminders import (
    close_reminder, create_standalone_reminder, snooze_reminder,
    sync_task_reminders, tick_reminders,
)
from operations.services import open_business_day
from operations.tasks import create_manual_task, set_task_status


@override_settings(REMINDERS_ENGINE_ENABLED=True, DUTY_NOTIFICATION_ROUTING_ENABLED=False)
class ReminderEngineTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='reminders-owner', phone='+963955889001',
            email='reminders-owner@example.org', password='pass',
        )
        self.tamer = User.objects.create_user(
            username='reminders-tamer', phone='+963955889002',
            role=User.Role.CASHIER, password='pass',
        )
        self.salwa = User.objects.create_user(
            username='reminders-salwa', phone='+963955889003',
            role=User.Role.CASHIER, password='pass',
        )
        self.waiter = User.objects.create_user(
            username='reminders-waiter', phone='+963955889004',
            role=User.Role.WAITER, password='pass',
        )
        self.day = open_business_day(actor=self.admin)
        set_staff_roster(
            self.day, actor=self.admin,
            user_ids=[self.admin.pk, self.tamer.pk, self.salwa.pk, self.waiter.pk],
        )
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        assign_duty_coverage(
            self.day, responsibility='operations',
            user_id=self.tamer.pk, actor=self.admin,
        )

    def make_reminder(self, **kwargs):
        values = {
            'actor': self.admin,
            'title': 'فحص ماكينة القهوة',
            'due_at': timezone.now() - timedelta(minutes=5),
            'responsibility': 'operations',
        }
        values.update(kwargs)
        return create_standalone_reminder(**values)

    def test_default_off_does_not_send_or_change_legacy_overdue_task(self):
        reminder = self.make_reminder()
        with override_settings(REMINDERS_ENGINE_ENABLED=False):
            self.assertEqual(tick_reminders()['sent'], 0)
        self.assertEqual(ReminderOccurrence.objects.count(), 0)
        reminder.refresh_from_db()
        self.assertIsNotNone(reminder.next_due_at)

    def test_one_time_reminder_in_app_only_and_idempotent(self):
        reminder = self.make_reminder()
        self.assertEqual(tick_reminders()['sent'], 1)
        self.assertEqual(tick_reminders()['sent'], 0)
        reminder.refresh_from_db()
        self.assertIsNone(reminder.next_due_at)
        occurrence = ReminderOccurrence.objects.get(reminder=reminder)
        self.assertEqual(occurrence.recipient_user, self.tamer)
        self.assertEqual(
            occurrence.notification_event.event_type,
            NotificationEvent.EventType.OPERATIONAL_REMINDER,
        )
        self.assertEqual(
            set(occurrence.notification_event.recipients.values_list('user_id', flat=True)),
            {self.tamer.pk},
        )
        self.assertFalse(NotificationLog.objects.filter(
            notification_event=occurrence.notification_event,
            channel=NotificationLog.Channel.BROWSER,
        ).exists())

    def test_uncovered_reminder_defers_without_broadcast_or_ledger_record(self):
        reminder = self.make_reminder(responsibility='service')
        outcome = tick_reminders()
        self.assertEqual(outcome['sent'], 0)
        self.assertEqual(outcome['deferred'], 1)
        self.assertFalse(ReminderOccurrence.objects.filter(reminder=reminder).exists())
        assign_duty_coverage(
            self.day, responsibility='service',
            user_id=self.waiter.pk, actor=self.admin,
        )
        self.assertEqual(tick_reminders()['sent'], 1)
        self.assertEqual(ReminderOccurrence.objects.get(reminder=reminder).recipient_user, self.waiter)

    def test_repeat_interval_skips_missed_intervals_and_deduplicates(self):
        reminder = self.make_reminder(
            repeat=Reminder.Repeat.INTERVAL, interval_minutes=60,
        )
        now = timezone.now()
        self.assertEqual(tick_reminders(now=now)['sent'], 1)
        reminder.refresh_from_db()
        self.assertGreater(reminder.next_due_at, now)
        self.assertEqual(tick_reminders(now=now)['sent'], 0)
        later = reminder.next_due_at + timedelta(seconds=1)
        self.assertEqual(tick_reminders(now=later)['sent'], 1)
        self.assertEqual(ReminderOccurrence.objects.filter(reminder=reminder).count(), 2)

    def test_daily_weekly_recurrence_keeps_local_time(self):
        due = timezone.now() - timedelta(days=15)
        for repeat in (Reminder.Repeat.DAILY, Reminder.Repeat.WEEKLY):
            reminder = self.make_reminder(
                title=f'روتين {repeat}', due_at=due, repeat=repeat,
            )
            self.assertEqual(tick_reminders()['sent'] >= 1, True)
            reminder.refresh_from_db()
            self.assertGreater(reminder.next_due_at, timezone.now())
            self.assertEqual(
                timezone.localtime(reminder.next_due_at).time().replace(tzinfo=None),
                timezone.localtime(due).time().replace(tzinfo=None),
            )

    def test_handover_follows_current_holder_not_past_checkin(self):
        reminder = self.make_reminder(
            assigned_to=self.tamer, repeat=Reminder.Repeat.INTERVAL,
            interval_minutes=1,
        )
        self.assertEqual(tick_reminders()['sent'], 1)
        request_duty_handover(
            self.day, responsibility='operations', to_user_id=self.salwa.pk,
            actor=self.tamer,
        )
        accept_duty_handover(
            self.day, responsibility='operations', actor=self.salwa,
        )
        reminder.next_due_at = timezone.now() - timedelta(seconds=1)
        reminder.save(update_fields=['next_due_at', 'updated_at'])
        self.assertEqual(tick_reminders()['sent'], 1)
        users = list(ReminderOccurrence.objects.filter(reminder=reminder).order_by(
            'created_at'
        ).values_list('recipient_user_id', flat=True))
        self.assertEqual(users, [self.tamer.pk, self.salwa.pk])

    def test_task_reminder_stops_when_original_task_is_done(self):
        task = create_manual_task(
            self.day, actor=self.admin, title='مهمة تشغيلية',
            due_at=timezone.now() - timedelta(minutes=3),
            assigned_to=self.tamer,
        )
        self.assertEqual(sync_task_reminders(self.day), 1)
        self.assertEqual(sync_task_reminders(self.day), 0)
        self.assertEqual(tick_reminders()['sent'], 1)
        task.refresh_from_db()
        self.assertEqual(task.reminder_count, 1)
        set_task_status(
            task, actor=self.admin, status=BusinessDayTask.Status.DONE,
        )
        reminder = task.reminder_schedule
        reminder.next_due_at = timezone.now() - timedelta(seconds=1)
        reminder.save(update_fields=['next_due_at', 'updated_at'])
        self.assertEqual(tick_reminders()['sent'], 0)
        self.assertEqual(ReminderOccurrence.objects.filter(reminder=reminder).count(), 1)

    def test_existing_wifi_board_task_has_no_automatic_deadline(self):
        from operations.tasks import sync_business_day_tasks
        sync_business_day_tasks(self.day)
        wifi_tasks = self.day.tasks.filter(fingerprint='wifi:board-pin', due_at__isnull=True)
        if wifi_tasks.exists():
            self.assertFalse(Reminder.objects.filter(source_task__in=wifi_tasks).exists())
        sync_task_reminders(self.day)
        if wifi_tasks.exists():
            self.assertFalse(Reminder.objects.filter(source_task__in=wifi_tasks).exists())

    def test_manual_complete_cancel_and_snooze_are_validated(self):
        reminder = self.make_reminder()
        with self.assertRaises(PermissionDenied):
            close_reminder(reminder, actor=self.waiter)
        with self.assertRaises(ValidationError):
            snooze_reminder(reminder, actor=self.admin, until=timezone.now()-timedelta(minutes=1))
        future = timezone.now() + timedelta(hours=2)
        snooze_reminder(reminder, actor=self.admin, until=future)
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_at, future)
        close_reminder(reminder, actor=self.admin)
        self.assertEqual(tick_reminders()['sent'], 0)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, Reminder.Status.COMPLETED)

    def test_bad_repeat_schedule_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_reminder(repeat=Reminder.Repeat.INTERVAL, interval_minutes=0)
        with self.assertRaises(ValidationError):
            self.make_reminder(repeat=Reminder.Repeat.DAILY, interval_minutes=10)
        with self.assertRaises(PermissionDenied):
            self.make_reminder(actor=self.waiter)

    def test_existing_tick_uses_engine_only_when_enabled(self):
        create_manual_task(
            self.day, actor=self.admin, title='اختبار أمر الجدولة',
            due_at=timezone.now()-timedelta(minutes=5),
            assigned_to=self.tamer,
        )
        from io import StringIO
        out = StringIO()
        call_command('business_day_tick', stdout=out)
        self.assertIn('reminders_created=', out.getvalue())
        self.assertTrue(ReminderOccurrence.objects.exists())
        self.assertEqual(self.day.tasks.filter(title_ar='اختبار أمر الجدولة').get().reminder_count, 1)

    def test_expired_reminder_never_sent(self):
        reminder = self.make_reminder()
        reminder.expires_at = timezone.now() - timedelta(seconds=1)
        reminder.save(update_fields=['expires_at', 'updated_at'])
        self.assertEqual(tick_reminders()['expired'], 1)
        self.assertFalse(ReminderOccurrence.objects.filter(reminder=reminder).exists())
