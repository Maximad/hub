from django import template
from django.utils import timezone

from core.models import OrderItem


register = template.Library()


# Launch defaults. These intentionally stay simple until real service data tells
# us whether station/product-specific targets are worth adding in v1.1.
UNACK_WARNING_MINUTES = 3
UNACK_LATE_MINUTES = 5
ACCEPTED_WARNING_MINUTES = 5
PREPARING_LATE_MINUTES = 12
READY_PICKUP_LATE_MINUTES = 3


def _elapsed_minutes(since, now):
    if not since:
        return 0
    return max(int((now - since).total_seconds() // 60), 0)


@register.simple_tag
def prep_timing(item, now=None):
    """Return the actionable timing state for the item's current prep stage."""
    now = now or timezone.now()
    status = item.prep_status
    since = item.created_at if status in {OrderItem.PrepStatus.NEW, OrderItem.PrepStatus.SENT} else item.updated_at
    minutes = _elapsed_minutes(since, now)

    result = {
        'minutes': minutes,
        'state': 'normal',
        'label': '',
        'css_class': '',
    }

    if status in {OrderItem.PrepStatus.NEW, OrderItem.PrepStatus.SENT}:
        if minutes >= UNACK_LATE_MINUTES:
            result.update(state='late', label=f'لم يتم الاستلام منذ {minutes} د', css_class='is-late')
        elif minutes >= UNACK_WARNING_MINUTES:
            result.update(state='warning', label=f'بانتظار الاستلام منذ {minutes} د', css_class='is-warning')
    elif status == OrderItem.PrepStatus.ACCEPTED and minutes >= ACCEPTED_WARNING_MINUTES:
        result.update(state='warning', label=f'لم يبدأ التحضير منذ {minutes} د', css_class='is-warning')
    elif status == OrderItem.PrepStatus.PREPARING and minutes >= PREPARING_LATE_MINUTES:
        result.update(state='late', label=f'التحضير متأخر — {minutes} د', css_class='is-late')
    elif status == OrderItem.PrepStatus.READY and minutes >= READY_PICKUP_LATE_MINUTES:
        result.update(state='warning', label=f'جاهز وينتظر الاستلام منذ {minutes} د', css_class='is-warning')

    return result
