from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import DailyClose, FinancialAccount, NotificationEvent
from operations.insights import detect_anomalies, send_owner_digest
from operations.models import BusinessDay, BusinessDayChecklistItem, HandoverNote
from operations.opening import (
    acknowledge_handover,
    create_handover_note,
    send_daily_code_reminders,
    set_staff_roster,
    unresolved_handover_notes,
    update_checklist_item,
)
from operations.services import open_business_day, reveal_daily_code, verify_daily_code


@override_settings(
    BUSINESS_DAY_CUTOFF_HOUR=4,
    BUSINESS_DAY_CODE_REMINDER_MINUTES=120,
    BUSINESS_DAY_OPENING_REMINDER_MINUTES=90,
    BUSINESS_DAY_CASH_DIFFERENCE_WARNING_SYP=5000,
    BUSINESS_DAY_DISCOUNT_WARNING_SYP=10000,
    BUSINESS_DAY_CANCELLATION_WARNING_COUNT=3,
)
class OpeningHandoverTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username='ops-owner', password='pass', email='owner@example.com', phone='+963955100001'
        )
        self.cashier = User.objects.create_user(
            username='ops-cashier', password='pass', phone='+963955100002', role=User.Role.CASHIER
        )
        self.waiter = User.objects.create_user(
            username='ops-waiter', password='pass', phone='+963955100003', role=User.Role.WAITER
        )
        self.other = User.objects.create_user(
            username='ops-other', password='pass', phone='+963955100004', role=User.Role.BARTENDER
        )
        self.provider = User.objects.create_user(
            username='ops-provider', password='pass', phone='+963955100005', role=User.Role.INTERNET_PROVIDER
        )

    def test_new_business_day_is_seeded_with_required_opening_checklist(self):
        day = open_business_day(actor=self.owner)
        items = day.checklist_items.order_by('sort_order')
        self.assertEqual(items.count(), 7)
        self.assertTrue(items.filter(code='staff_roster', is_required=True).exists())
        self.assertTrue(items.filter(code='internet_status', is_required=True).exists())

    def test_roster_and_daily_code_use_create_lightweight_checkin(self):
        day = open_business_day(actor=self.owner)
        set_staff_roster(day, actor=self.owner, user_ids=[self.cashier.pk, self.waiter.pk])
        assignment = day.staff_assignments.get(user=self.waiter)
        self.assertIsNone(assignment.checked_in_at)

        day.refresh_from_db()
        code = reveal_daily_code(day, user=self.waiter)
        self.assertTrue(verify_daily_code(day, code, user=self.waiter))

        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.checked_in_at)
        self.assertFalse(day.staff_assignments.filter(user=self.other).exists())
        self.assertFalse(day.staff_assignments.filter(user=self.provider).exists())

    def test_opening_completes_only_after_all_required_items_are_confirmed(self):
        day = open_business_day(actor=self.owner)
        set_staff_roster(day, actor=self.owner, user_ids=[self.owner.pk])
        for item in day.checklist_items.exclude(code='staff_roster'):
            update_checklist_item(
                item,
                actor=self.owner,
                status=BusinessDayChecklistItem.Status.DONE,
                note='تم الفحص',
            )
        day.refresh_from_db()
        self.assertIsNotNone(day.opening_completed_at)
        self.assertEqual(day.opening_completed_by, self.owner)

        item = day.checklist_items.exclude(code='staff_roster').first()
        update_checklist_item(item, actor=self.owner, status=BusinessDayChecklistItem.Status.PENDING)
        day.refresh_from_db()
        self.assertIsNone(day.opening_completed_at)

    def test_handover_note_remains_visible_until_resolved(self):
        day = open_business_day(actor=self.owner)
        set_staff_roster(day, actor=self.owner, user_ids=[self.cashier.pk, self.waiter.pk])
        note = create_handover_note(
            day,
            actor=self.cashier,
            message='تحقق من ماكينة الإسبريسو قبل بداية الازدحام.',
            priority=HandoverNote.Priority.HIGH,
            assigned_to=self.waiter,
        )
        self.assertIn(note, list(unresolved_handover_notes(day)))
        acknowledge_handover(note, actor=self.waiter)
        note.refresh_from_db()
        self.assertEqual(note.status, HandoverNote.Status.ACKNOWLEDGED)
        self.assertIn(note, list(unresolved_handover_notes(day)))

    def test_code_reminder_targets_on_duty_staff_only_and_contains_no_secret(self):
        day = open_business_day(actor=self.owner)
        set_staff_roster(day, actor=self.owner, user_ids=[self.cashier.pk, self.waiter.pk])
        day.refresh_from_db()
        secret = reveal_daily_code(day, user=self.cashier)
        reminded = send_daily_code_reminders(day, actor=self.owner, min_interval_minutes=1)
        self.assertEqual(reminded, 1)
        event = NotificationEvent.objects.filter(title_ar__startswith='تذكير فريق يوم').latest('created_at')
        recipients = set(event.recipients.values_list('user_id', flat=True))
        self.assertEqual(recipients, {self.waiter.pk})
        self.assertNotIn(secret, event.message_ar)

    def test_plaintext_daily_code_is_scrubbed_before_notification_persistence(self):
        day = open_business_day(actor=self.owner)
        code = reveal_daily_code(day, user=self.owner)
        event = NotificationEvent.objects.filter(title_ar__startswith='رمز الفريق ليوم').latest('created_at')
        self.assertNotIn(code, event.message_ar)
        self.assertIn('افتح صفحة اليوم التشغيلي', event.message_ar)

    def test_anomaly_rules_flag_cash_difference_and_incomplete_opening(self):
        day = open_business_day(actor=self.owner)
        account = FinancialAccount.objects.create(
            code='ops-cash', name_ar='صندوق تشغيل', account_type=FinancialAccount.AccountType.ASSET, is_active=True
        )
        DailyClose.objects.create(
            account=account,
            business_date=day.business_date,
            status=DailyClose.Status.CLOSED,
            is_finalized=True,
            cash_difference_syp=7000,
        )
        codes = {item['code'] for item in detect_anomalies(day)}
        self.assertIn('cash_difference', codes)
        self.assertIn('opening_incomplete', codes)

    def test_owner_digest_targets_superusers_not_provider(self):
        day = open_business_day(actor=self.owner)
        event = send_owner_digest(day, actor=self.cashier)
        recipients = set(event.recipients.values_list('user_id', flat=True))
        self.assertEqual(recipients, {self.owner.pk})
        self.assertNotIn(self.provider.pk, recipients)
        self.assertIn(str(day.business_date), event.title_ar)

    def test_business_day_tick_dry_run_does_not_send_reminders(self):
        day = open_business_day(actor=self.owner)
        set_staff_roster(day, actor=self.owner, user_ids=[self.waiter.pk])
        before = NotificationEvent.objects.count()
        output = StringIO()
        call_command('business_day_tick', '--dry-run', stdout=output)
        self.assertEqual(NotificationEvent.objects.count(), before)
        self.assertIn(f'business_day={day.business_date}', output.getvalue())
