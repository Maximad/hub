"""Read-only, source-aware membership Internet display.

Do not infer a member's Internet balance from the legacy subscription field.
Each entitlement is a separate commercial allowance with its own validity.
"""
from collections import defaultdict

from django.utils import timezone

from core.models import InternetEntitlement, InternetPackage
from members.models import MembershipSubscription


def membership_subscription_rows(subscriptions, *, at=None):
    """Materialize status and per-entitlement balances for staff/member screens.

    The returned remaining minutes are *unspent*, not necessarily immediately
    available: active session reservations, daily caps and validity can narrow
    the amount authorized for a new session. This function never mutates data.
    """
    at = at or timezone.now()
    subscriptions = list(subscriptions)
    if not subscriptions:
        return []

    by_subscription = defaultdict(list)
    for ent in (InternetEntitlement.objects
                .filter(subscription_id__in=[sub.pk for sub in subscriptions])
                .order_by('created_at', 'pk')):
        by_subscription[ent.subscription_id].append(ent)

    labels = dict(MembershipSubscription.Status.choices)
    access_labels = dict(InternetEntitlement.Status.choices)
    rows = []
    for sub in subscriptions:
        sub_status = sub.effective_status(at)
        entitlements = []
        for ent in by_subscription[sub.pk]:
            ent_status = ent.effective_status(at)
            available = (sub_status == MembershipSubscription.Status.ACTIVE
                         and ent_status == InternetEntitlement.Status.ACTIVE
                         and (ent.valid_from is None or ent.valid_from <= at))
            if (ent.access_mode == InternetPackage.AccessMode.MEMBERSHIP_CREDIT
                    and ent.total_minutes_allowed is None):
                # Legacy subscription credit is authoritative only for this mode.
                remaining = max(sub.remaining_internet_minutes or 0, 0)
                source = 'رصيد العضوية القديم'
            elif ent.total_minutes_allowed is not None:
                remaining = ent.minutes_remaining
                source = 'رصيد المخصصات'
            else:
                remaining = None
                source = 'بدون رصيد دقائق محدد'
            entitlements.append({
                'remaining_minutes': remaining,
                'source': source,
                'status_label': access_labels[ent_status] if available else (
                    'بانتظار التفعيل' if sub_status == MembershipSubscription.Status.ACTIVE
                    and ent_status == InternetEntitlement.Status.PENDING else 'غير متاح حالياً'),
                'available': available,
                'valid_until': ent.valid_until,
            })
        rows.append({
            'subscription': sub,
            'status_label': labels[sub_status],
            'internet': entitlements,
        })
    return rows
