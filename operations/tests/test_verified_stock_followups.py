"""Pilot stock-review tasks read only real count evidence and current Active Duty."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import ActivityLog, InventoryItem, StockMovement, Payment
from operations.models import BusinessDayTask, BusinessDayDutyCoverage
from operations.opening import set_staff_roster
from operations.services import open_business_day
from operations.duty import assign_duty_coverage


class VerifiedStockFollowupsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="stock-task-admin", email="stocktask@example.org",
            phone="+963955910008", password="pass",
        )
        self.cashier = User.objects.create_user(
            username="stock-task-cashier", phone="+963955910009",
            role=User.Role.CASHIER, password="pass",
        )
        self.day = open_business_day(actor=self.admin)
        set_staff_roster(self.day, actor=self.admin, user_ids=[self.cashier.pk])
        self.item = InventoryItem.objects.create(
            name_ar="بن عربي", code="TASK-COFFEE", unit=InventoryItem.Unit.KG,
            current_quantity=0,
        )

    def run_cmd(self, *, apply=False, ids=None, actor="stock-task-admin"):
        buf = StringIO()
        args = ["--ids", ids or str(self.item.pk)]
        if apply:
            args.extend(["--apply", "--actor", actor])
        call_command("sync_verified_stock_followups", *args, stdout=buf)
        return buf.getvalue()

    def test_preview_is_non_mutating_and_unknown_zero_is_not_stockout(self):
        output = self.run_cmd()
        self.assertIn("PREVIEW ONLY", output)
        self.assertIn("create:", output)
        self.assertIn("unassigned", output)
        self.assertFalse(BusinessDayTask.objects.filter(
            fingerprint__startswith="verified-stock:"
        ).exists())
        self.assertFalse(ActivityLog.objects.filter(
            action__startswith="stock_followup."
        ).exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_explicit_apply_idempotent_and_unassigned_without_checked_in_duty(self):
        self.assertIn("create:", self.run_cmd(apply=True))
        task = BusinessDayTask.objects.get(
            fingerprint=f"verified-stock:needs-count:{self.item.pk}"
        )
        self.assertIsNone(task.assigned_to_id)
        self.assertIsNone(task.due_at)
        self.assertEqual(task.source_type, "InventoryItem")
        self.assertEqual(self.run_cmd(apply=True).count("already_exists:"), 1)
        self.assertEqual(
            BusinessDayTask.objects.filter(fingerprint__startswith="verified-stock:").count(), 1
        )
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_assigns_only_to_checked_in_authorized_duty(self):
        self.day.staff_assignments.filter(user=self.cashier).update(checked_in_at=timezone.now())
        assign_duty_coverage(
            self.day, responsibility=BusinessDayDutyCoverage.Responsibility.OPERATIONS,
            user_id=self.cashier.pk, actor=self.admin,
        )
        self.run_cmd(apply=True)
        task = BusinessDayTask.objects.get(
            fingerprint=f"verified-stock:needs-count:{self.item.pk}"
        )
        self.assertEqual(task.assigned_to_id, self.cashier.pk)

    def test_recount_resolves_pending_task_only_after_actual_confirmation(self):
        self.run_cmd(apply=True)
        fp = f"verified-stock:needs-count:{self.item.pk}"
        ActivityLog.objects.create(
            actor=self.cashier, action="inventory_stock_count_confirmed",
            details={"item_id": self.item.pk, "verified_no_change": True},
        )
        self.assertIn("resolved_by_count:", self.run_cmd())
        self.assertEqual(BusinessDayTask.objects.get(fingerprint=fp).status, "pending")
        self.run_cmd(apply=True)
        self.assertEqual(BusinessDayTask.objects.get(fingerprint=fp).status, "waived")
        self.assertIn("verified_no_action:", self.run_cmd())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_completed_task_not_overwritten_and_invalid_input_rejected(self):
        self.run_cmd(apply=True)
        task = BusinessDayTask.objects.get(
            fingerprint=f"verified-stock:needs-count:{self.item.pk}"
        )
        task.status = BusinessDayTask.Status.DONE
        task.save(update_fields=["status"])
        self.assertIn("already_exists:", self.run_cmd(apply=True))
        self.assertEqual(task.pk, BusinessDayTask.objects.get(pk=task.pk).pk)
        for invalid in ["0", "-1", "1,1", "1,invalid", "1,2,3,4,5,6,7,8,9,10,11"]:
            with self.subTest(ids=invalid), self.assertRaises(CommandError):
                self.run_cmd(ids=invalid)
        with self.assertRaises(CommandError):
            call_command("sync_verified_stock_followups", "--ids", str(self.item.pk), "--apply")

    def test_checked_out_or_revoked_assignment_cannot_auto_assign(self):
        self.day.staff_assignments.filter(user=self.cashier).update(checked_in_at=timezone.now())
        assign_duty_coverage(
            self.day, responsibility=BusinessDayDutyCoverage.Responsibility.OPERATIONS,
            user_id=self.cashier.pk, actor=self.admin,
        )
        self.day.staff_assignments.filter(user=self.cashier).update(checked_in_at=None)
        self.run_cmd(apply=True)
        self.assertIsNone(BusinessDayTask.objects.get(
            fingerprint=f"verified-stock:needs-count:{self.item.pk}"
        ).assigned_to_id)
