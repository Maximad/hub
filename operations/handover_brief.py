"""Read-only handover briefing from the canonical open review-case system.

No duplicate tasks, notifications, assignments or financial side effects.
Review access matches the existing staff operational-tasks page.
"""
from .models import OperationalReviewCase
from .review_cases import ISSUE_TITLES, case_duty_holder


MAX_ITEMS = 8
MAX_SCAN = 100


def visible_handover_reviews(day, *, user, can_manage):
    """Small current-state view, not an audited acceptance of the work.

    A proposed recipient sees nothing until they actually accept Active Duty.
    Managers see the queue regardless of missing duty coverage. The same
    cases remain open across Business Days until evidence-based resolution.
    """
    if day is None or not getattr(user, "is_authenticated", False):
        return {"items": [], "truncated": False}
    rows = (
        OperationalReviewCase.objects.filter(
            status=OperationalReviewCase.Status.OPEN
        )
        .order_by("-reviewed_at", "-pk")[:MAX_SCAN]
    )
    items = []
    truncated = False
    for case in rows:
        if not can_manage:
            holder = case_duty_holder(day, case)
            if holder is None or holder.pk != user.pk:
                continue
        if len(items) >= MAX_ITEMS:
            truncated = True
            break
        items.append({
            "id": case.pk,
            "title": ISSUE_TITLES.get(case.issue_code, "متابعة تشغيلية"),
            "label": case.source_label,
        })
    return {"items": items, "truncated": truncated}
