"""Bounded same-day showtimes and weekly repetition with no duplicate occurrences."""
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def build_performance_schedule(start, end, extra_showtimes='', repeat_weeks='1'):
    try:
        weeks = int(repeat_weeks or 1)
    except (ValueError, TypeError):
        raise ValidationError('عدد الأسابيع غير صالح.')
    if not 1 <= weeks <= 12 or str(repeat_weeks or '1').strip() != str(weeks):
        raise ValidationError('عدد الأسابيع يجب أن يكون بين 1 و12.')
    if end and end <= start:
        raise ValidationError('نهاية العرض يجب أن تكون بعد بدايته.')
    duration = end - start if end else None
    starts = [start]
    for line in (extra_showtimes or '').splitlines():
        raw = line.strip()
        if not raw:
            continue
        dt = parse_datetime(raw.replace(' ', 'T'))
        if dt is None:
            raise ValidationError('اكتب الموعد الإضافي بصيغة YYYY-MM-DD HH:MM.')
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        starts.append(dt)
    if len(starts) * weeks > 48:
        raise ValidationError('الحد الأقصى 48 عرضاً ضمن الفعالية.')
    output = []
    seen = set()
    for week in range(weeks):
        for base in starts:
            show_start = base + timedelta(weeks=week)
            if show_start in seen:
                raise ValidationError('يوجد عرض مكرر في التاريخ والوقت نفسيهما.')
            seen.add(show_start)
            output.append((show_start, show_start + duration if duration else None))
    output.sort(key=lambda x: x[0])
    if output[0][0] != start:
        raise ValidationError('موعد البداية الأساسي يجب أن يكون أول موعد في الفعالية.')
    return output
