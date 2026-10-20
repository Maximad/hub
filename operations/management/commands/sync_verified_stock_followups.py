"""Manually requested, bounded physical-count follow-ups for the current Business Day.

Default preview makes NO writes. Explicit --apply only changes BusinessDayTask
and ActivityLog. Never creates inventory movements or financial transactions.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ActivityLog, InventoryItem
from core.services.operational_completeness import counted_inventory_ids
from operations.duty import current_duty_users
from operations.models import BusinessDay, BusinessDayDutyCoverage, BusinessDayTask


FINGERPRINT_PREFIX = "verified-stock:needs-count:"
AUTO_RESOLVED = "أُغلقت تلقائياً بعد توثيق الجرد الفعلي للمادة."


def _active_day():
    return BusinessDay.objects.filter(
        status__in=[
            BusinessDay.Status.OPEN,
            BusinessDay.Status.REOPENED,
            BusinessDay.Status.CLOSING,
        ]
    ).order_by("-business_date").first()


def _parse_ids(raw):
    parts = [part.strip() for part in (raw or "").split(",")]
    if not parts or len(parts) > 10 or any(not p.isascii() or not p.isdecimal() for p in parts):
        raise CommandError("Enter 1-10 distinct integer material IDs with --ids 5,171,173")
    ids = [int(p) for p in parts]
    if len(ids) != len(set(ids)) or any(p <= 0 for p in ids):
        raise CommandError("Material IDs must be positive and unique")
    return ids


def _assignee(day):
    # Eligible current duty is a valid checked-in assignment; receipt of this
    # inventory work additionally requires the exact stock-count capability.
    from accounts.permissions import user_has_capability

    users = current_duty_users(day, BusinessDayDutyCoverage.Responsibility.OPERATIONS)
    if len(users) == 1 and user_has_capability(users[0], "inventory_stock_count"):
        return users[0]
    return None


def _synchronize(day, ids, *, apply):
    from django.utils import timezone

    items = list(InventoryItem.objects.filter(pk__in=ids, is_active=True).order_by("pk"))
    if set(item.pk for item in items) != set(ids):
        raise CommandError("One or more materials are missing or inactive; no updates made")
    verified = counted_inventory_ids(ids)
    assigned = _assignee(day)
    results = []
    for item in items:
        fp = FINGERPRINT_PREFIX + str(item.pk)
        task = BusinessDayTask.objects.select_for_update().filter(
            business_day=day, fingerprint=fp,
        ).first()
        if item.pk in verified:
            if task and task.status == BusinessDayTask.Status.PENDING:
                action = "resolved_by_count"
                if apply:
                    task.status = BusinessDayTask.Status.WAIVED
                    task.completion_note = AUTO_RESOLVED
                    task.completed_at = timezone.now()
                    task.save(update_fields=[
                        "status", "completion_note", "completed_at", "updated_at",
                    ])
                    ActivityLog.objects.create(
                        action="stock_followup.resolved",
                        details={"business_day_id": day.pk, "item_id": item.pk, "task_id": task.pk},
                    )
            else:
                action = "verified_no_action"
        elif task is None:
            action = "create"
            if apply:
                task = BusinessDayTask.objects.create(
                    business_day=day,
                    fingerprint=fp,
                    kind=BusinessDayTask.Kind.MANUAL,
                    title_ar=f"جرد فعلي: {item.name_ar}",
                    details=(
                        f"تأكد من كمية المادة ({item.code or item.pk}) عبر شاشة جرد المخزون. "
                        "الصفر التاريخي ليس إثباتاً لنفاد المادة."
                    ),
                    priority="normal",
                    due_at=None,  # no automatic overdue notifications
                    is_required=False,
                    assigned_to=assigned,
                    responsibility_role="",
                    source_type="InventoryItem",
                    source_id=str(item.pk),
                    metadata={"origin": "verified-stock-pilot", "reason": "needs_count"},
                )
                ActivityLog.objects.create(
                    action="stock_followup.created",
                    details={
                        "business_day_id": day.pk, "item_id": item.pk, "task_id": task.pk,
                        "assigned_to_id": getattr(assigned, "pk", None),
                    },
                )
        else:
            # Do not reopen waived/done tasks or silently rewrite manual
            # assignment; the user explicitly owns their task decisions.
            action = "already_exists"
        results.append({
            "item_id": item.pk,
            "name_ar": item.name_ar,
            "action": action,
            "assigned_to_id": getattr(assigned, "pk", None) if action == "create" else (
                task.assigned_to_id if task else None
            ),
            "uncovered": bool(action == "create" and assigned is None),
        })
    return results


class Command(BaseCommand):
    help = "Preview/create up to 10 missing verified-count tasks with guarded Active Duty assignment."

    def add_arguments(self, parser):
        parser.add_argument("--ids", required=True, help="1-10 actual inventory IDs separated by commas")
        parser.add_argument("--apply", action="store_true", help="Commit bounded tasks; defaults to no writes")

    def handle(self, *args, **options):
        ids = _parse_ids(options["ids"])
        with transaction.atomic():
            day = _active_day()
            if day is None:
                raise CommandError("No open Business Day; first open and roster the operating day")
            results = _synchronize(day, ids, apply=options["apply"])
            if not options["apply"]:
                transaction.set_rollback(True)
        self.stdout.write("APPLIED" if options["apply"] else "PREVIEW ONLY")
        for row in results:
            self.stdout.write(
                f"{row['action']}: #{row['item_id']} {row['name_ar']}; "
                f"assignee={row['assigned_to_id'] or 'unassigned'}"
            )
        self.stdout.write(
            "No stock quantity, purchase, payment, recipe activation or financial posting changed."
        )
