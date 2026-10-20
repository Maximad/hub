"""Bounded Internet review cases must never provision, charge or leak credentials."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import InternetSession, Payment, StockMovement
from internet.models import InternetSessionNetworkOperation
from internet.session_network_backends import PROVISION_ERROR, PROVISIONED
from operations.models import BusinessDayTask, OperationalReviewCase
from operations.review_cases import run_review_sync
from operations.services import open_business_day


@override_settings(
    ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class InternetSessionReviewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_superuser(
            username="session-review-admin", password="x",
            phone="+963955816001", email="session-reviews@example.org",
        )
        self.day = open_business_day(actor=self.manager)
        self.session = InternetSession.objects.create(
            start_time=timezone.now(),
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
            network_status=PROVISION_ERROR,
            guest_name="Sensitive Guest",
            guest_phone="0999999999",
        )
        self.job = InternetSessionNetworkOperation.objects.create(
            session=self.session,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
            status=InternetSessionNetworkOperation.Status.FAILED,
            attempt_count=3,
            idempotency_key="review-failed-provision-1",
            last_error="password=confidential secret",
        )

    def sync(self, *, apply=False, include_recent=False):
        return run_review_sync(
            self.day, product_ids=[], inventory_ids=[],
            internet_ids=[self.session.pk] if not include_recent else [],
            include_recent=include_recent, dry_run=not apply,
        )

    def test_preview_rolls_back_cases_and_tasks_without_mutating_network_state(self):
        result = self.sync()
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(result["counts"]["task_created"], 1)
        self.assertFalse(OperationalReviewCase.objects.exists())
        self.assertFalse(BusinessDayTask.objects.filter(source_type="OperationalReviewCase").exists())
        self.job.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.job.status, InternetSessionNetworkOperation.Status.FAILED)
        self.assertEqual(self.job.attempt_count, 3)
        self.assertEqual(self.session.network_status, PROVISION_ERROR)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_explicit_sync_is_deduplicated_and_redacts_guest_and_network_details(self):
        first = self.sync(apply=True)
        self.assertEqual(first["counts"]["created"], 1)
        case = OperationalReviewCase.objects.get()
        self.assertEqual(case.source_type, OperationalReviewCase.SourceType.INTERNET_SESSION)
        self.assertEqual(case.issue_code, OperationalReviewCase.IssueCode.NETWORK_REVIEW)
        self.assertEqual(case.source_label, f"جلسة إنترنت #{self.session.pk}")
        self.assertEqual(case.evidence, {"source": "recent_actual_operation_or_selected_record"})
        task = BusinessDayTask.objects.get(source_type="OperationalReviewCase")
        text = " ".join([case.source_label, str(case.evidence), task.title_ar,
                         task.details, str(task.metadata)])
        self.assertNotIn("Sensitive Guest", text)
        self.assertNotIn("0999999999", text)
        self.assertNotIn("confidential", text)
        self.assertNotIn("password", text.lower())
        self.assertIsNone(task.due_at)  # no forced overdue notifications
        self.assertIsNone(task.assigned_to_id)
        second = self.sync(apply=True)
        self.assertEqual(second["counts"]["created"], 0)
        self.assertEqual(second["counts"]["task_created"], 0)
        self.assertEqual(OperationalReviewCase.objects.count(), 1)

        self.client.force_login(self.manager)
        page = self.client.get(reverse("staff_operational_tasks"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f"جلسة إنترنت #{self.session.pk}")
        self.assertContains(page, reverse("staff_internet_session", args=[self.session.pk]))
        self.assertNotContains(page, "Sensitive Guest")
        self.assertNotContains(page, "confidential")

    def test_worker_recovery_resolves_and_later_failure_reopens_without_side_effect(self):
        self.sync(apply=True)
        case = OperationalReviewCase.objects.get()
        task = BusinessDayTask.objects.get(source_type="OperationalReviewCase")
        self.job.status = InternetSessionNetworkOperation.Status.SUCCEEDED
        self.job.save(update_fields=["status", "updated_at"])
        self.session.network_status = PROVISIONED
        self.session.save(update_fields=["network_status", "updated_at"])
        result = self.sync(apply=True)
        self.assertEqual(result["counts"]["resolved"], 1)
        case.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(case.status, OperationalReviewCase.Status.RESOLVED)
        self.assertEqual(task.status, BusinessDayTask.Status.WAIVED)

        self.session.network_status = PROVISION_ERROR
        self.session.save(update_fields=["network_status", "updated_at"])
        InternetSessionNetworkOperation.objects.create(
            session=self.session,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
            status=InternetSessionNetworkOperation.Status.FAILED,
            attempt_count=3,
            idempotency_key="review-failed-provision-recurrence",
        )
        result = self.sync(apply=True)
        self.assertEqual(result["counts"]["reopened"], 1)
        self.assertEqual(result["counts"]["task_created"], 0)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_retry_noise_and_finished_sessions_do_not_generate_incidents(self):
        self.job.attempt_count = 2
        self.job.save(update_fields=["attempt_count", "updated_at"])
        self.assertEqual(self.sync(apply=True)["counts"]["created"], 0)
        self.job.attempt_count = 3
        self.job.save(update_fields=["attempt_count", "updated_at"])
        self.session.status = InternetSession.Status.ENDED
        self.session.save(update_fields=["status", "updated_at"])
        self.assertEqual(self.sync(apply=True)["counts"]["created"], 0)
        self.session.status = InternetSession.Status.ACTIVE
        self.session.network_provider = InternetSession.NetworkProvider.MANUAL
        self.session.save(update_fields=["status", "network_provider", "updated_at"])
        self.assertEqual(self.sync(apply=True)["counts"]["created"], 0)

    def test_recent_mode_requires_actual_recent_failed_worker_operation(self):
        self.assertEqual(self.sync(apply=True, include_recent=True)["counts"]["created"], 1)
        OperationalReviewCase.objects.all().delete()
        BusinessDayTask.objects.filter(source_type="OperationalReviewCase").delete()
        InternetSessionNetworkOperation.objects.filter(pk=self.job.pk).update(
            created_at=timezone.now() - timedelta(days=45),
        )
        self.assertEqual(self.sync(apply=True, include_recent=True)["counts"]["created"], 0)

    def test_existing_command_can_preview_specific_session_without_actor(self):
        output = StringIO()
        call_command(
            "review_operational_followups", "--no-recent",
            "--internet-session-id", str(self.session.pk), stdout=output,
        )
        self.assertIn("PREVIEW ONLY", output.getvalue())
        self.assertIn(f"internet_session:{self.session.pk}:network_review", output.getvalue())
        self.assertFalse(OperationalReviewCase.objects.exists())
        with self.assertRaises(CommandError):
            call_command(
                "review_operational_followups", "--no-recent",
                "--internet-session-id", str(self.session.pk), "--apply",
            )
