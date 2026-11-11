"""Bounded staff scheduling; one event can have several independently bookable shows."""
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import Room
from events.models import Event, EventPerformance
from reservations.models import EventAdmission, EventAttendanceRequest, Reservation

DAMASCUS = ZoneInfo('Asia/Damascus')
ACTIVE_BOOKINGS = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED)


def _local_occurrence(starts_at, offset, frequency):
    local = timezone.localtime(starts_at, DAMASCUS)
    if frequency == 'weekly':
        return local + timedelta(days=7 * offset)
    if frequency == 'monthly':
        target = local.month - 1 + offset
        year, month = local.year + target // 12, target % 12 + 1
        day = min(local.day, calendar.monthrange(year, month)[1])
        return local.replace(year=year, month=month, day=day)
    return local


@transaction.atomic
def add_performances(event_id, *, actor, starts_at, ends_at=None, capacity=None,
                     frequency='once', count=1, room_id=None):
    """Generate at most 12 showtimes. Repeating this command never duplicates a time."""
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'events'):
        raise ValidationError('لا تملك صلاحية جدولة العروض.')
    if frequency not in {'once', 'weekly', 'monthly'}:
        raise ValidationError('اختر تكراراً صالحاً.')
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 12:
        raise ValidationError('عدد العروض يجب أن يكون من 1 إلى 12.')
    if frequency == 'once' and count != 1:
        raise ValidationError('اختر تكراراً أسبوعياً أو شهرياً لإضافة عروض متعددة.')
    if timezone.is_naive(starts_at):
        raise ValidationError('وقت العرض يجب أن يتضمن منطقة زمنية.')
    if ends_at and (timezone.is_naive(ends_at) or ends_at <= starts_at):
        raise ValidationError('يجب أن تكون نهاية العرض بعد بدايته.')
    if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0):
        raise ValidationError('السعة يجب أن تكون عدداً موجباً.')
    event = Event.objects.select_for_update().get(pk=event_id)
    if event.status in {Event.Status.CANCELLED, Event.Status.COMPLETED}:
        raise ValidationError('لا يمكن إضافة عروض لفعالية منتهية أو ملغاة.')
    room = None
    if room_id:
        room = Room.objects.filter(pk=room_id).first()
        if room is None:
            raise ValidationError('المساحة المختارة غير موجودة.')
    if not EventPerformance.objects.filter(event=event).exists():
        # Old programmatic/admin-created events may not yet have a show row.
        original = EventPerformance.objects.create(event=event, starts_at=event.starts_at,
            ends_at=event.ends_at, room=event.room, capacity=event.capacity or None)
        Reservation.objects.filter(event=event, performance__isnull=True).update(performance=original)
        EventAttendanceRequest.objects.filter(event=event, performance__isnull=True).update(
            performance=original, performance_uuid=original.uuid)
    duration = ends_at - starts_at if ends_at else None
    added = []
    for index in range(count):
        start = _local_occurrence(starts_at, index, frequency)
        finish = start + duration if duration else None
        existing = EventPerformance.objects.filter(event=event, starts_at=start).first()
        if existing:
            if existing.status != EventPerformance.Status.SCHEDULED:
                raise ValidationError('أحد المواعيد ملغى؛ لا يعاد تفعيله تلقائياً.')
            added.append(existing)
            continue
        show = EventPerformance(event=event, starts_at=start, ends_at=finish, room=room,
                                capacity=capacity)
        show.full_clean()
        show.save()
        added.append(show)
    first = min(show.starts_at for show in EventPerformance.objects.filter(
        event=event, status=EventPerformance.Status.SCHEDULED))
    if first < event.starts_at:
        event.starts_at = first
        event.save(update_fields=['starts_at', 'updated_at'])
    return added


@transaction.atomic
def cancel_performance(performance_id, *, actor):
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'events'):
        raise ValidationError('لا تملك صلاحية إلغاء موعد.')
    show = EventPerformance.objects.select_related('event').get(pk=performance_id)
    Event.objects.select_for_update().get(pk=show.event_id)
    show = EventPerformance.objects.select_for_update().get(pk=performance_id)
    if show.status == EventPerformance.Status.CANCELLED:
        return show
    if Reservation.objects.filter(performance=show, status__in=ACTIVE_BOOKINGS).exists():
        raise ValidationError('هناك حجوزات نشطة لهذا العرض. عالج الحجوزات والمدفوعات أولاً.')
    if EventAttendanceRequest.objects.filter(performance=show, status__in=('pending', 'approved')).exists():
        raise ValidationError('هناك طلبات حضور مرتبطة بهذا العرض. عالجها أولاً.')
    if Reservation.objects.filter(performance=show).filter(
            ticket_order__isnull=False).exists():
        raise ValidationError('توجد فواتير تذاكر لهذا العرض؛ يجب تسويتها قبل الإلغاء.')
    if EventAdmission.objects.filter(reservation__performance=show).exists():
        raise ValidationError('تم تسجيل حضور ضيوف لهذا العرض؛ لا يمكن إلغاء موعده.')
    show.status = EventPerformance.Status.CANCELLED
    show.save(update_fields=['status'])
    return show
