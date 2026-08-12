"""Transactional reservation-domain operations."""
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from core.models import Room, TableArea
from events.models import Event
from .models import Reservation

ACTIVE_STATUSES = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED)


def _regular_end_time(reservation):
    if reservation.end_time:
        return reservation.end_time
    minutes = getattr(settings, 'RESERVATION_DEFAULT_DURATION_MINUTES', 120)
    if not isinstance(minutes, int) or minutes <= 0:
        raise ValidationError({'end_time': 'مدة الحجز الافتراضية غير صالحة.'})
    end = datetime.combine(reservation.reservation_date, reservation.start_time) + timedelta(minutes=minutes)
    if end.date() != reservation.reservation_date:
        raise ValidationError({'end_time': 'يجب أن ينتهي الحجز في يوم بدايته.'})
    return end.time()


def _lock_regular_resource(reservation):
    room_id = reservation.room_id
    if reservation.table_area_id:
        table = TableArea.objects.select_for_update().select_related('room').get(pk=reservation.table_area_id)
        room_id = table.room_id
        reservation.room_id = room_id
    if not room_id:
        raise ValidationError({'room': 'يرجى اختيار الغرفة أو الطاولة.'})
    # The room is the shared lock for both exclusive and individual-table writes.
    Room.objects.select_for_update().get(pk=room_id)


def _check_regular_conflict(reservation):
    rows = Reservation.objects.select_for_update().filter(
        reservation_type=Reservation.ReservationType.REGULAR,
        status__in=ACTIVE_STATUSES,
        reservation_date=reservation.reservation_date,
        start_time__lt=reservation.end_time,
        end_time__gt=reservation.start_time,
    ).exclude(pk=reservation.pk)
    if reservation.table_area_id:
        rows = rows.filter(Q(table_area_id=reservation.table_area_id) | Q(room_id=reservation.room_id, table_area__isnull=True))
    else:
        rows = rows.filter(room_id=reservation.room_id)
    if rows.exists():
        raise ValidationError({'__all__': 'يتعارض هذا الحجز مع حجز نشط في الوقت والمكان نفسيهما.'})


def _check_event_capacity(reservation):
    if not reservation.event_id:
        raise ValidationError({'event': 'يرجى اختيار الفعالية.'})
    event = Event.objects.select_for_update().get(pk=reservation.event_id)
    if event.capacity is None:
        return
    used = Reservation.objects.filter(
        event_id=event.pk, reservation_type=Reservation.ReservationType.EVENT,
        status__in=ACTIVE_STATUSES,
    ).exclude(pk=reservation.pk).aggregate(total=Sum('party_size'))['total'] or 0
    if used + reservation.party_size > event.capacity:
        raise ValidationError({'party_size': 'عدد الحضور يتجاوز سعة الفعالية.'})


def _validate_for_status(reservation):
    # Besides model validation, this normalizes form-style date/time strings
    # before interval arithmetic and database comparisons.
    reservation.full_clean()
    if reservation.reservation_type == Reservation.ReservationType.REGULAR:
        reservation.end_time = _regular_end_time(reservation)
        _lock_regular_resource(reservation)
        if reservation.status in ACTIVE_STATUSES:
            _check_regular_conflict(reservation)
    elif reservation.status == Reservation.Status.CONFIRMED:
        _check_event_capacity(reservation)
    reservation.full_clean()


@transaction.atomic
def create_reservation(reservation):
    _validate_for_status(reservation)
    reservation.save()
    return reservation


@transaction.atomic
def change_reservation_status(reservation_id, new_status):
    allowed = {choice for choice, _label in Reservation.Status.choices}
    if new_status not in allowed:
        raise ValidationError({'status': 'حالة الحجز غير صالحة.'})
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    reservation.status = new_status
    _validate_for_status(reservation)
    reservation.save(update_fields=['status', 'end_time', 'room', 'updated_at'])
    return reservation
