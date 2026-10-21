"""Handover briefing is permission-scoped, read-only and acceptance-gated."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import ActivityLog, InventoryItem, StockMovement, Payment
from operations.duty import (
    accept_duty_handover, assign_duty_coverage, request_duty_handover,
)
from operations.models import (
    BusinessDayDutyCoverage, OperationalReviewCase,
)
from operations.opening import set_staff_roster
from operations.services import open_business_day


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class HandoverReviewBriefTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="handover-brief-admin", phone="+963955900181",
            email="handover@example.com", password="test",
        )
        self.outgoing = User.objects.create_user(
            username="handover-brief-outgoing", role=User.Role.WAITER,
            phone="+963955900182", password="test",
        )
        self.incoming = User.objects.create_user(
            username="handover-brief-incoming", role=User.Role.WAITER,
            phone="+963955900183", password="test",
        )
        self.other = User.objects.create_user(
            username="handover-brief-other", role=User.Role.WAITER,
            phone="+963955900184", password="test",
        )
        for person in (self.outgoing, self.incoming):
            UserCapabilityOverride.objects.create(
                user=person, capability="inventory_stock_count", allowed=True,
            )
        self.day = open_business_day(actor=self.admin)
        set_staff_roster(
            self.day, actor=self.admin,
            user_ids=[self.outgoing.pk, self.incoming.pk, self.other.pk],
        )
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        self.material = InventoryItem.objects.create(
            name_ar="بن بحاجة لجرد", code="HANDOVER-BEANS",
            unit=InventoryItem.Unit.KG, current_quantity=0,
        )
        self.case = OperationalReviewCase.objects.create(
            source_type=OperationalReviewCase.SourceType.INVENTORY,
            source_id=self.material.pk,
            issue_code=OperationalReviewCase.IssueCode.NEEDS_COUNT,
            source_label=self.material.name_ar,
            status=OperationalReviewCase.Status.OPEN,
        )
        self.url = reverse("staff_close_day")

    def test_manager_sees_open_case_without_duty_and_get_does_not_mutate(self):
        self.client.force_login(self.admin)
        logs = ActivityLog.objects.count()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "متابعات مفتوحة عند التسليم")
        self.assertContains(response, self.material.name_ar)
        self.assertEqual(OperationalReviewCase.objects.count(), 1)
        self.assertEqual(ActivityLog.objects.count(), logs)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_only_accepted_checked_in_holder_sees_case(self):
        assign_duty_coverage(
            self.day,
            responsibility=BusinessDayDutyCoverage.Responsibility.OPERATIONS,
            user_id=self.outgoing.pk, actor=self.admin,
        )
        self.client.force_login(self.outgoing)
        self.assertContains(self.client.get(self.url), self.material.name_ar)
        self.client.force_login(self.incoming)
        self.assertNotContains(self.client.get(self.url), self.material.name_ar)
        request_duty_handover(
            self.day,
            responsibility=BusinessDayDutyCoverage.Responsibility.OPERATIONS,
            to_user_id=self.incoming.pk, actor=self.outgoing,
        )
        self.assertNotContains(self.client.get(self.url), self.material.name_ar)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url), self.material.name_ar)
        accept_duty_handover(
            self.day,
            responsibility=BusinessDayDutyCoverage.Responsibility.OPERATIONS,
            actor=self.incoming,
        )
        self.client.force_login(self.incoming)
        self.assertContains(self.client.get(self.url), self.material.name_ar)
        self.client.force_login(self.outgoing)
        self.assertNotContains(self.client.get(self.url), self.material.name_ar)

    def test_resolution_removes_case_without_closing_work_on_page_read(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), self.material.name_ar)
        self.case.status = OperationalReviewCase.Status.RESOLVED
        self.case.resolved_at = timezone.now()
        self.case.save(update_fields=["status", "resolved_at"])
        self.assertNotContains(self.client.get(self.url), self.material.name_ar)

    def test_list_is_bounded_and_requires_permitted_identity(self):
        for idx in range(12):
            OperationalReviewCase.objects.create(
                source_type=OperationalReviewCase.SourceType.INVENTORY,
                source_id=100000 + idx,
                issue_code=OperationalReviewCase.IssueCode.NEEDS_COUNT,
                source_label=f"Material {idx}",
                status=OperationalReviewCase.Status.OPEN,
            )
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertEqual(len(page.context["handover_review_brief"]["items"]), 8)
        self.assertTrue(page.context["handover_review_brief"]["truncated"])
        self.client.force_login(self.other)
        page = self.client.get(self.url)
        self.assertEqual(page.context["handover_review_brief"]["items"], [])
