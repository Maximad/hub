"""Reservation queue, creation, status transitions, and visit check-in."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import require_staff_capability
from core.models import Member, TableArea
from reservations.models import Reservation
from reservations.services import change_reservation_status, check_in_reservation
from core.views_legacy import (
    staff_reservation_new,
    staff_reservation_tables,
)


def _validation_message(error):
    if hasattr(error, 'message_dict'):
        return ' '.join(
            message
            for messages_for_field in error.message_dict.values()
            for message in messages_for_field
        )
    return ' '.join(getattr(error, 'messages', [str(error)]))


@require_staff_capability('reservations')
def staff_reservations(request):
    """Keep the existing reservation grouping while exposing visit state."""
    today = timezone.localdate()
    all_rows = (
        Reservation.objects.select_related(
            'room',
            'table_area__room',
            'event__room',
            'visit',
            'visit__table__room',
            'visit__member',
        )
        .order_by('reservation_date', 'start_time')
    )
    active = all_rows.exclude(status=Reservation.Status.CANCELLED)
    today_rows = active.filter(
        Q(reservation_type=Reservation.ReservationType.EVENT, event__starts_at__date=today)
        | Q(reservation_type=Reservation.ReservationType.REGULAR, reservation_date=today)
    )
    upcoming_rows = active.exclude(status=Reservation.Status.COMPLETED).filter(
        Q(reservation_type=Reservation.ReservationType.EVENT, event__starts_at__date__gt=today)
        | Q(reservation_type=Reservation.ReservationType.REGULAR, reservation_date__gt=today)
    )
    past_cancelled_rows = all_rows.filter(
        Q(status__in=[Reservation.Status.CANCELLED, Reservation.Status.COMPLETED])
        | Q(reservation_type=Reservation.ReservationType.EVENT, event__starts_at__date__lt=today)
        | Q(reservation_type=Reservation.ReservationType.REGULAR, reservation_date__lt=today)
    )
    return render(
        request,
        'staff/reservations.html',
        {
            'today_rows': today_rows,
            'upcoming_rows': upcoming_rows,
            'past_cancelled_rows': past_cancelled_rows,
        },
    )


@require_staff_capability('reservations')
@require_http_methods(['GET', 'POST'])
def staff_reservation_detail(request, reservation_id):
    reservation = get_object_or_404(
        Reservation.objects.select_related(
            'room',
            'table_area__room',
            'event__room',
            'visit',
            'visit__table__room',
            'visit__member',
        ),
        pk=reservation_id,
    )

    if request.method == 'POST' and request.POST.get('action') == 'checkin':
        try:
            visit, created = check_in_reservation(
                reservation.pk,
                actor=request.user,
                table_id=request.POST.get('table') or None,
            )
        except ValidationError as error:
            messages.error(request, _validation_message(error))
            return redirect('staff_reservation_detail', reservation_id=reservation.pk)

        if created:
            messages.success(request, f'تم تسجيل الوصول وفتح {visit}.')
        else:
            messages.info(request, f'الحجز مسجّل الوصول بالفعل في {visit}.')
        return redirect(f"{reverse('staff_home')}?visit={visit.public_code}")

    effective_room = reservation.effective_room
    checkin_tables = TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar')
    if effective_room:
        checkin_tables = checkin_tables.filter(room=effective_room)

    phone = (reservation.phone or '').strip()
    matching_member = Member.objects.filter(phone=phone).first() if phone else None
    return render(
        request,
        'staff/reservation_detail.html',
        {
            'reservation': reservation,
            'statuses': Reservation.Status.choices,
            'checkin_tables': checkin_tables,
            'matching_member': matching_member,
        },
    )


@require_staff_capability('reservations')
def staff_reservation_status(request, reservation_id):
    """Preserve the reservation state machine and surface validation to staff."""
    if request.method != 'POST':
        raise Http404()
    reservation = get_object_or_404(Reservation, pk=reservation_id)
    new_status = request.POST.get('status', '').strip()
    correction = request.POST.get('action') == 'correct'
    reason = request.POST.get('reason', '').strip()
    try:
        change_reservation_status(
            reservation.pk,
            new_status,
            actor=request.user,
            correction=correction,
            reason=reason,
        )
    except ValidationError as error:
        messages.error(request, _validation_message(error))
        return redirect('staff_reservation_detail', reservation_id=reservation.pk)
    messages.success(request, 'تم تحديث حالة الحجز.')
    return redirect('staff_reservation_detail', reservation_id=reservation.pk)
