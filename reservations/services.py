"""Transactional reservation-domain operations."""
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import ActivityLog, HubVisit, Member, Room, TableArea
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


def _checkin_table(reservation, table_id):
    chosen_id = table_id or reservation.table_area_id
    if not chosen_id:
        return None
    try:
        table = TableArea.objects.select_for_update().select_related('room').get(pk=chosen_id)
    except (TableArea.DoesNotExist, TypeError, ValueError):
        raise ValidationError({'table': 'الطاولة المختارة غير صالحة.'})
    effective_room = reservation.effective_room
    if effective_room and table.room_id != effective_room.pk:
        raise ValidationError({'table': 'الطاولة المختارة لا تتبع لمساحة الحجز.'})
    return table


def _matching_member(reservation):
    phone = (reservation.phone or '').strip()
    if not phone:
        return None
    return Member.objects.select_for_update().filter(phone=phone).first()


@transaction.atomic
def check_in_reservation(reservation_id, *, actor, table_id=None):
    """Create exactly one operational HubVisit for a confirmed reservation.

    The reservation row is the idempotency lock. A repeated check-in returns the
    already-open visit; a previously completed/closed visit is never recreated.
    """
    reservation = (
        Reservation.objects.select_for_update(of=('self',))
        .select_related('visit', 'table_area__room', 'room', 'event__room')
        .get(pk=reservation_id)
    )
    if reservation.status != Reservation.Status.CONFIRMED:
        raise ValidationError({'status': 'يجب تأكيد الحجز قبل تسجيل الوصول.'})

    if reservation.visit_id:
        visit = HubVisit.objects.select_for_update().get(pk=reservation.visit_id)
        if visit.status == HubVisit.Status.OPEN:
            return visit, False
        raise ValidationError({'visit': 'تم تسجيل وصول هذا الحجز سابقاً وانتهت جلسته.'})

    arrival_date = reservation.effective_date
    if arrival_date and arrival_date != timezone.localdate():
        raise ValidationError({'date': 'يمكن تسجيل الوصول في يوم الحجز فقط.'})

    table = _checkin_table(reservation, table_id)
    if table:
        occupied = (
            HubVisit.objects.select_for_update()
            .filter(table=table, status=HubVisit.Status.OPEN)
            .first()
        )
        if occupied:
            raise ValidationError({'table': f'الطاولة مرتبطة حالياً بـ {occupied}. اختر طاولة أخرى أو افتح الجلسة الموجودة.'})

    member = _matching_member(reservation)
    if member:
        existing_member_visit = (
            HubVisit.objects.select_for_update()
            .filter(member=member, status=HubVisit.Status.OPEN)
            .first()
        )
        if existing_member_visit:
            raise ValidationError({'member': f'لدى العضو جلسة مفتوحة بالفعل: {existing_member_visit}.'})

    visit = HubVisit.objects.create(
        table=table,
        member=member,
        notes=(reservation.notes or '').strip(),
        created_by=actor,
    )
    reservation.visit = visit
    reservation.save(update_fields=['visit', 'updated_at'])
    ActivityLog.objects.create(
        actor=actor,
        action='reservation.checked_in',
        details={
            'reservation_id': reservation.pk,
            'visit_id': visit.pk,
            'table_id': visit.table_id,
            'member_id': visit.member_id,
        },
    )
    return visit, True


@transaction.atomic
def create_reservation(reservation):
    _validate_for_status(reservation)
    reservation.save()
    return reservation


@transaction.atomic
def change_reservation_status(reservation_id, new_status, *, actor, correction=False, reason=''):
    """Compatibility entry point; all mutations use the locked state machine."""
    if correction:
        return Reservation.correct_status(
            reservation_id, actor=actor, new_status=new_status, reason=reason,
        )
    return Reservation.transition_status(reservation_id, actor=actor, new_status=new_status)


def complete_reservation_for_visit(visit_id, *, actor):
    """Complete a linked confirmed reservation when its operational visit closes."""
    reservation = Reservation.objects.filter(visit_id=visit_id).first()
    if reservation and reservation.status == Reservation.Status.CONFIRMED:
        return change_reservation_status(
            reservation.pk,
            Reservation.Status.COMPLETED,
            actor=actor,
        )
    return reservation
