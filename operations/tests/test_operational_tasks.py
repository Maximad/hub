from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import InventoryItem, NotificationEvent
from events.models import Event
from reservations.models import Reservation

from operations.insights import detect_anomalies
from operations.models import BusinessDayTask, OperationalTaskTemplate
from operations.services import open_business_day
from operations.tasks import (
    create_manual_task,
    send_overdue_task_reminders,
    set_task_status,
    sync_business_day_tasks,
)


@override_settings(
    BUSINESS_DAY_CUTOFF_HOUR=4,
    BUSINESS_DAY_EVENT_PREP_LEAD_MINUTES=120,
    BUSINESS_DAY_RESERVATION_PREP_LEAD_MINUTES=30,
    BUSINESS_DAY_TASK_REMINDER_MINUTES=60,
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class OperationalTaskTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='task-admin', password='pass', email='task-admin@example.com', phone='+963955200001'
        )
        self.cashier = User.objects.create_user(
            username='task-cashier', password='pass', phone='+963955200002', role=User.Role.CASHIER
        )
        self.waiter = User.objects.create_user(
            username='task-waiter', password='pass', phone='+963955200003', role=User.Role.WAITER
        )
        self.provider = User.objects.create_user(
            username='task-provider', password='pass', phone='+963955200004', role=User.Role.INTERNET_PROVIDER
        )
        self.business_date = date(2026, 9, 22)

    def aware(self, day, hour, minute=0):
        return timezone.make_aware(
            datetime.combine(day, time(hour, minute)),
            timezone=timezone.get_current_timezone(),
        )

    def test_recurring_template_materializes_once_and_sync_is_idempotent(self):
        template = OperationalTaskTemplate.objects.create(
            title_ar='فحص برادات البار',
            weekdays=[self.business_date.weekday()],
            due_time=time(11, 0),
            priority=OperationalTaskTemplate.Priority.HIGH,
            responsibility_role=get_user_model().Role.BARTENDER,
            is_required=True,
            created_by=self.admin,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'template:{template.pk}')
        self.assertEqual(task.kind, BusinessDayTask.Kind.RECURRING)
        self.assertTrue(task.is_required)
        self.assertEqual(timezone.localtime(task.due_at).hour, 11)

        result = sync_business_day_tasks(day, actor=self.admin)
        self.assertEqual(result['created'], 0)
        self.assertEqual(day.tasks.filter(fingerprint=f'template:{template.pk}').count(), 1)

    def test_recurring_task_due_before_cutoff_lands_on_next_calendar_date(self):
        template = OperationalTaskTemplate.objects.create(
            title_ar='إغلاق الموسيقى الخارجية',
            weekdays=[self.business_date.weekday()],
            due_time=time(2, 0),
            created_by=self.admin,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'template:{template.pk}')
        local_due = timezone.localtime(task.due_at)
        self.assertEqual(local_due.date(), self.business_date + timedelta(days=1))
        self.assertEqual(local_due.hour, 2)

    def test_published_event_creates_required_prep_task_without_mutating_event(self):
        event = Event.objects.create(
            title_ar='ليلة موسيقية',
            starts_at=self.aware(self.business_date, 19),
            ends_at=self.aware(self.business_date, 22),
            status=Event.Status.PUBLISHED,
            capacity=40,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'event:{event.pk}:prep')
        self.assertEqual(task.kind, BusinessDayTask.Kind.EVENT)
        self.assertTrue(task.is_required)
        self.assertEqual(timezone.localtime(task.due_at).hour, 17)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PUBLISHED)
        self.assertEqual(event.capacity, 40)

    def test_after_midnight_confirmed_reservation_belongs_to_previous_business_day(self):
        reservation_date = self.business_date + timedelta(days=1)
        reservation = Reservation.objects.create(
            reservation_type=Reservation.ReservationType.REGULAR,
            name='حجز بعد منتصف الليل',
            phone='0999000000',
            reservation_date=reservation_date,
            start_time=time(1, 30),
            end_time=time(2, 30),
            party_size=4,
            status=Reservation.Status.CONFIRMED,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'reservation:{reservation.pk}:prep')
        local_due = timezone.localtime(task.due_at)
        self.assertEqual(local_due.date(), reservation_date)
        self.assertEqual((local_due.hour, local_due.minute), (1, 0))
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CONFIRMED)

    def test_low_stock_items_are_grouped_into_one_review_task_and_auto_close_when_resolved(self):
        item = InventoryItem.objects.create(
            name_ar='حبوب قهوة اختبار',
            item_type=InventoryItem.ItemType.INGREDIENT,
            unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('1.000'),
            low_stock_threshold=Decimal('2.000'),
            is_active=True,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint='inventory:low-stock-review')
        self.assertEqual(task.kind, BusinessDayTask.Kind.INVENTORY)
        self.assertIn(item.pk, task.metadata['item_ids'])

        item.current_quantity = Decimal('5.000')
        item.save(update_fields=['current_quantity', 'updated_at'])
        result = sync_business_day_tasks(day, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(result['auto_waived'], 1)
        self.assertEqual(task.status, BusinessDayTask.Status.WAIVED)
        self.assertIn('زوال سبب المهمة', task.completion_note)

    def test_cancelled_event_auto_closes_pending_generated_task(self):
        event = Event.objects.create(
            title_ar='فعالية قابلة للإلغاء',
            starts_at=self.aware(self.business_date, 18),
            status=Event.Status.PUBLISHED,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'event:{event.pk}:prep')
        event.status = Event.Status.CANCELLED
        event.save(update_fields=['status', 'updated_at'])
        sync_business_day_tasks(day, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.WAIVED)

    def test_auto_waived_generated_task_reopens_if_source_returns(self):
        event = Event.objects.create(
            title_ar='فعالية تعود بعد الإلغاء',
            starts_at=self.aware(self.business_date, 20),
            status=Event.Status.PUBLISHED,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'event:{event.pk}:prep')
        event.status = Event.Status.CANCELLED
        event.save(update_fields=['status', 'updated_at'])
        sync_business_day_tasks(day, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.WAIVED)

        event.status = Event.Status.PUBLISHED
        event.save(update_fields=['status', 'updated_at'])
        sync_business_day_tasks(day, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)
        self.assertEqual(task.completion_note, '')

    def test_staff_completed_generated_task_is_not_reopened_by_sync(self):
        event = Event.objects.create(
            title_ar='فعالية تم تحضيرها',
            starts_at=self.aware(self.business_date, 21),
            status=Event.Status.PUBLISHED,
        )
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = day.tasks.get(fingerprint=f'event:{event.pk}:prep')
        set_task_status(task, actor=self.admin, status=BusinessDayTask.Status.DONE, note='تم التحضير')
        event.status = Event.Status.CANCELLED
        event.save(update_fields=['status', 'updated_at'])
        sync_business_day_tasks(day, actor=self.admin)
        event.status = Event.Status.PUBLISHED
        event.save(update_fields=['status', 'updated_at'])
        sync_business_day_tasks(day, actor=self.admin)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.DONE)
        self.assertEqual(task.completion_note, 'تم التحضير')

    def test_manual_task_waiver_requires_reason(self):
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = create_manual_task(day, actor=self.admin, title='اختبار مهمة')
        with self.assertRaises(ValidationError):
            set_task_status(task, actor=self.admin, status=BusinessDayTask.Status.WAIVED, note='')
        set_task_status(task, actor=self.admin, status=BusinessDayTask.Status.WAIVED, note='لم تعد مطلوبة')
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.WAIVED)
        self.assertEqual(task.completed_by, self.admin)

    def test_overdue_reminder_targets_assigned_staff_and_is_rate_limited(self):
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = create_manual_task(
            day,
            actor=self.admin,
            title='مهمة متأخرة',
            due_at=timezone.now() - timedelta(minutes=10),
            assigned_to=self.waiter,
        )
        count = send_overdue_task_reminders(day, actor=self.admin, min_interval_minutes=60)
        self.assertEqual(count, 1)
        event = NotificationEvent.objects.filter(title_ar__startswith='مهمة تشغيلية متأخرة').latest('created_at')
        recipients = set(event.recipients.values_list('user_id', flat=True))
        self.assertEqual(recipients, {self.waiter.pk})
        self.assertEqual(send_overdue_task_reminders(day, actor=self.admin, min_interval_minutes=60), 0)
        task.refresh_from_db()
        self.assertEqual(task.reminder_count, 1)

    def test_overdue_and_required_tasks_are_advisory_anomalies(self):
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        create_manual_task(
            day,
            actor=self.admin,
            title='مهمة مطلوبة ومتأخرة',
            due_at=timezone.now() - timedelta(minutes=5),
            is_required=True,
        )
        codes = {item['code'] for item in detect_anomalies(day)}
        self.assertIn('operational_tasks_overdue', codes)
        self.assertIn('required_tasks_pending', codes)

    def test_staff_console_permissions_and_manager_manual_task_creation(self):
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_operational_tasks'))
        self.assertEqual(response.status_code, 200)

        self.client.force_login(self.provider)
        response = self.client.get(reverse('staff_operational_tasks'))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.cashier)
        response = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'manual_add',
            'title_ar': 'مهمة من الواجهة',
            'priority': 'high',
            'is_required': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(day.tasks.filter(title_ar='مهمة من الواجهة', kind=BusinessDayTask.Kind.MANUAL).exists())

    def test_non_manager_cannot_complete_unassigned_task(self):
        day = open_business_day(actor=self.admin, business_date=self.business_date)
        task = create_manual_task(day, actor=self.admin, title='للمدير فقط')
        self.client.force_login(self.waiter)
        response = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'status',
            'task_id': task.pk,
            'status': 'done',
        })
        self.assertEqual(response.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)
