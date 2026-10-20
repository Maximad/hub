"""Purchase follow-up observes actual unreversed receipts, never paper reconstructions."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog, InventoryItem, OperationsImportReceipt, Payment,
    PostingBatch, Purchase, PurchaseItem, PurchaseReceipt, PurchaseReceiptLine,
    StockMovement,
)
from operations.models import BusinessDayTask, OperationalReviewCase
from operations.review_cases import run_review_sync
from operations.services import open_business_day


@override_settings(
    ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class ReceivedPurchaseReviewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_superuser(
            username="receipt-review-admin", email="receipt@example.org",
            phone="+963955801045", password="pass",
        )
        self.day = open_business_day(actor=self.manager)
        self.item = InventoryItem.objects.create(
            name_ar="بن جديد", code="PR-RECEIVED-COFFEE",
            unit=InventoryItem.Unit.KG, current_quantity=Decimal("1.000"),
        )
        self.purchase = Purchase.objects.create(
            business_date=timezone.localdate(), supplier_name="",
            created_by=self.manager,
        )
        self.line = PurchaseItem.objects.create(
            purchase=self.purchase, inventory_item=self.item,
            quantity=Decimal("1.000"), unit=self.item.unit,
            unit_cost_syp=Decimal("0"),
        )
        self.receipt = self.make_receipt(price="0", key="test-purchase-receipt-a")

    def make_receipt(self, *, price, key):
        receipt = PurchaseReceipt.objects.create(
            purchase=self.purchase, business_date=timezone.localdate(),
            actor=self.manager, idempotency_key=key,
        )
        line = PurchaseReceiptLine.objects.create(
            receipt=receipt, purchase_item=self.line,
            received_quantity=Decimal("1.000"),
        )
        StockMovement.objects.create(
            inventory_item=self.item, business_date=timezone.localdate(),
            movement_type=StockMovement.MovementType.PURCHASE_RECEIVED,
            direction=StockMovement.Direction.IN,
            quantity=Decimal("1.000"), unit=self.item.unit,
            unit_cost_syp=Decimal(str(price)), total_value_syp=Decimal(str(price)),
            related_purchase=self.purchase, related_purchase_item=self.line,
            purchase_receipt_line=line, created_by=self.manager,
        )
        return receipt

    def sync(self, *, apply=False, purchase_ids=None, recent=False):
        return run_review_sync(
            self.day, product_ids=[], inventory_ids=[], internet_ids=[],
            purchase_ids=([self.purchase.pk] if purchase_ids is None else purchase_ids),
            include_recent=recent, dry_run=not apply, actor=self.manager,
        )

    def test_preview_rolls_back_all_review_tasks_and_does_not_touch_money_or_stock(self):
        before_quantity = self.item.current_quantity
        result = self.sync()
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(result["counts"]["task_created"], 1)
        self.assertFalse(OperationalReviewCase.objects.exists())
        self.assertFalse(BusinessDayTask.objects.filter(source_type="OperationalReviewCase").exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, before_quantity)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PostingBatch.objects.exists())
        self.assertFalse(ActivityLog.objects.filter(action="operational.review_sync").exists())

    def test_apply_is_idempotent_and_links_to_existing_purchase_page(self):
        self.sync(apply=True)
        case = OperationalReviewCase.objects.get()
        self.assertEqual(case.source_type, OperationalReviewCase.SourceType.PURCHASE)
        self.assertEqual(case.issue_code, OperationalReviewCase.IssueCode.RECEIPT_REVIEW)
        self.assertEqual(case.source_label, f"شراء مستلم #{self.purchase.pk}")
        task = BusinessDayTask.objects.get(source_type="OperationalReviewCase")
        self.assertIsNone(task.due_at)
        self.assertIsNone(task.assigned_to_id)
        self.assertEqual(task.source_id, str(case.pk))
        repeat = self.sync(apply=True)
        self.assertEqual(repeat["counts"]["created"], 0)
        self.assertEqual(repeat["counts"]["task_created"], 0)
        self.assertEqual(OperationalReviewCase.objects.count(), 1)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("staff_operational_tasks"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse("staff_purchase_detail", args=[self.purchase.pk]))

    def test_editing_old_purchase_item_price_does_not_certify_posted_receipt(self):
        self.sync(apply=True)
        self.purchase.supplier_name = "مورد معلوم"
        self.purchase.save(update_fields=["supplier_name", "updated_at"])
        self.line.unit_cost_syp = Decimal("200")
        self.line.save(update_fields=["unit_cost_syp", "updated_at"])
        result = self.sync(apply=True)
        self.assertEqual(result["counts"]["resolved"], 0)
        self.assertEqual(OperationalReviewCase.objects.get().status, OperationalReviewCase.Status.OPEN)
        original = StockMovement.objects.get(purchase_receipt_line__receipt=self.receipt)
        self.assertEqual(original.unit_cost_syp, Decimal("0"))
        self.assertFalse(Payment.objects.exists())

    def test_reversed_receipt_and_new_valid_receipt_resolve_without_task_duplication(self):
        self.sync(apply=True)
        self.purchase.supplier_name = "مورد معلوم"
        self.purchase.save(update_fields=["supplier_name", "updated_at"])
        self.receipt.reversed_at = timezone.now()
        self.receipt.save(update_fields=["reversed_at", "updated_at"])
        self.make_receipt(price="230", key="test-purchase-receipt-b")
        result = self.sync(apply=True)
        self.assertEqual(result["counts"]["resolved"], 1)
        case = OperationalReviewCase.objects.get()
        task = BusinessDayTask.objects.get(source_type="OperationalReviewCase")
        self.assertEqual(case.status, OperationalReviewCase.Status.RESOLVED)
        self.assertEqual(task.status, BusinessDayTask.Status.WAIVED)
        self.assertEqual(OperationalReviewCase.objects.count(), 1)
        self.assertFalse(Payment.objects.exists())

    def test_draft_purchase_and_imported_history_do_not_create_case(self):
        self.receipt.reversed_at = timezone.now()
        self.receipt.save(update_fields=["reversed_at", "updated_at"])
        self.assertEqual(self.sync(apply=True)["counts"]["created"], 0)
        self.receipt.reversed_at = None
        self.receipt.save(update_fields=["reversed_at", "updated_at"])
        OperationsImportReceipt.objects.create(
            purchase=self.purchase, import_key="ledger-paper-2025-a",
            intent={"source": "notebook"},
        )
        self.assertEqual(self.sync(apply=True)["counts"]["created"], 0)
        self.assertFalse(OperationalReviewCase.objects.exists())

    def test_purchase_is_never_auto_discovered_by_recent_mode(self):
        result = self.sync(apply=True, purchase_ids=[], recent=True)
        self.assertEqual(result["counts"]["created"], 0)
        self.assertFalse(OperationalReviewCase.objects.exists())

    def test_command_preview_requires_explicit_purchase_id_and_apply_actor(self):
        out = StringIO()
        call_command(
            "review_operational_followups", "--no-recent",
            "--purchase-id", str(self.purchase.pk),
            stdout=out,
        )
        self.assertIn(f"created purchase:{self.purchase.pk}:receipt_review", out.getvalue())
        self.assertFalse(OperationalReviewCase.objects.exists())
        with self.assertRaises(CommandError):
            call_command(
                "review_operational_followups", "--no-recent",
                "--purchase-id", str(self.purchase.pk),
                "--apply",
            )

    def test_missing_purchase_identity_fails_entire_batch(self):
        with self.assertRaises(Exception):
            self.sync(apply=True, purchase_ids=[self.purchase.pk, self.purchase.pk + 999_999])
        self.assertFalse(OperationalReviewCase.objects.exists())
