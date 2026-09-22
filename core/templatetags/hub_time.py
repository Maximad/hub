from datetime import date, datetime

from django import template
from django.utils import timezone


register = template.Library()

_MONTHS = (
    '', 'كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران',
    'تموز', 'آب', 'أيلول', 'تشرين الأول', 'تشرين الثاني', 'كانون الأول',
)


def _local(value):
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


@register.filter
def ar_date(value):
    if not value:
        return '—'
    value = _local(value)
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return value
    return f'{value.day} {_MONTHS[value.month]} {value.year}'


@register.filter
def ar_datetime(value):
    if not value:
        return '—'
    value = _local(value)
    if not isinstance(value, datetime):
        return ar_date(value)
    return f'{value.day} {_MONTHS[value.month]} {value.year} · {value:%H:%M}'


@register.filter
def ar_time(value):
    if not value:
        return '—'
    value = _local(value)
    if isinstance(value, datetime):
        return value.strftime('%H:%M')
    return str(value)


def _unit(value, one, two, plural):
    if value == 1:
        return one
    if value == 2:
        return two
    return f'{value} {plural}'


@register.filter
def ar_since(value, now=None):
    """Compact Arabic elapsed-time label for staff operational surfaces."""
    if not value:
        return '—'
    value = _local(value)
    if not isinstance(value, datetime):
        return value
    current = _local(now) if isinstance(now, datetime) else timezone.localtime()
    seconds = max(0, int((current - value).total_seconds()))
    if seconds < 60:
        return 'أقل من دقيقة'
    minutes = seconds // 60
    if minutes < 60:
        return _unit(minutes, 'دقيقة', 'دقيقتان', 'دقائق')
    hours = minutes // 60
    if hours < 24:
        return _unit(hours, 'ساعة', 'ساعتان', 'ساعات')
    days = hours // 24
    return _unit(days, 'يوم', 'يومان', 'أيام')
