from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from operations.models import BusinessDay, BusinessDayTask
from operations.opening import maybe_send_opening_reminder, send_daily_code_reminders
from operations.services import current_business_date, open_business_day
from operations.tasks import send_overdue_task_reminders, sync_business_day_tasks


class Command(BaseCommand):
    help = 'Run one idempotent Business Day automation tick for tasks and reminders.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        active = BusinessDay.objects.filter(
            status__in=[BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED]
        ).order_by('-business_date').first()

        if active is None and getattr(settings, 'BUSINESS_DAY_AUTO_OPEN_ENABLED', False):
            local_now = timezone.localtime()
            auto_open_hour = int(getattr(settings, 'BUSINESS_DAY_AUTO_OPEN_HOUR', 10))
            if local_now.hour >= auto_open_hour:
                if dry_run:
                    self.stdout.write(f'would_open={current_business_date()}')
                    return
                active = open_business_day(actor=None)
                self.stdout.write(f'opened={active.business_date}')

        if active is None:
            self.stdout.write('no_active_business_day')
            return

        if dry_run:
            pending_receipts = active.code_receipts.filter(
                code_version=active.code_version,
                viewed_at__isnull=True,
                first_used_at__isnull=True,
            ).count()
            pending_opening = active.checklist_items.filter(
                is_required=True,
                status='pending',
            ).count()
            pending_tasks = active.tasks.filter(status=BusinessDayTask.Status.PENDING).count()
            overdue_tasks = active.tasks.filter(
                status=BusinessDayTask.Status.PENDING,
                due_at__isnull=False,
                due_at__lte=timezone.now(),
            ).count()
            self.stdout.write(
                f'business_day={active.business_date} pending_code_receipts={pending_receipts} '
                f'pending_opening_items={pending_opening} pending_tasks={pending_tasks} overdue_tasks={overdue_tasks}'
            )
            return

        task_sync = sync_business_day_tasks(active)
        task_reminders = send_overdue_task_reminders(active)
        reminder_minutes = int(getattr(settings, 'BUSINESS_DAY_CODE_REMINDER_MINUTES', 120))
        reminder_due = (
            not active.code_issued_at
            or timezone.now() >= active.code_issued_at + timedelta(minutes=max(reminder_minutes, 1))
        )
        code_reminders = send_daily_code_reminders(active) if reminder_due else 0
        opening_reminder = maybe_send_opening_reminder(active)
        self.stdout.write(
            f'business_day={active.business_date} tasks_created={task_sync["created"]} '
            f'tasks_auto_waived={task_sync["auto_waived"]} task_reminders={task_reminders} '
            f'code_reminders={code_reminders} opening_reminder={int(opening_reminder)}'
        )
