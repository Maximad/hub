"""Read-only event booking operations report.

All numbers represent reservations or ticket price snapshots, not cashier receipts.
Financial revenue must come from authoritative posted settlement records only.
"""
from django.db.models import Count, F, Sum

from .models import (
    EventAdmission, Reservation, ReservationPaymentArrangement,
    ReservationTicketAllocation,
)

ACTIVE = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED)


def event_booking_report(event_id):
    bookings = Reservation.objects.filter(
        event_id=event_id,
        reservation_type=Reservation.ReservationType.EVENT,
    )
    status_rows = bookings.values('status').annotate(
        reservations=Count('pk'), people=Sum('party_size'),
    )
    statuses = {
        row['status']: {'reservations': row['reservations'], 'people': row['people'] or 0}
        for row in status_rows
    }
    active = bookings.filter(status__in=ACTIVE)
    admitted = (
        EventAdmission.objects.filter(reservation__in=bookings)
        .aggregate(total=Sum('arrived_count'))['total'] or 0
    )
    guarantees = ReservationPaymentArrangement.objects.filter(
        reservation__in=active,
        mode=ReservationPaymentArrangement.Mode.STAFF_GUARANTEE,
    ).aggregate(people=Sum('reservation__party_size'), bookings=Count('pk'))
    door = ReservationPaymentArrangement.objects.filter(
        reservation__in=active,
        mode=ReservationPaymentArrangement.Mode.PAY_AT_DOOR,
    ).aggregate(people=Sum('reservation__party_size'), bookings=Count('pk'))

    tier_rows = list(
        ReservationTicketAllocation.objects.filter(
            reservation__in=active,
        ).values(
            'ticket_type_id', 'ticket_type__name_ar', 'ticket_type__capacity',
            'reservation__performance_id', 'reservation__performance__starts_at',
        ).annotate(
            assigned=Sum('quantity'),
            face_value=Sum(F('quantity') * F('unit_price_syp')),
        ).order_by('ticket_type_id')
    )
    for row in tier_rows:
        from django.utils import timezone
        start = row['reservation__performance__starts_at']
        row['show_label'] = timezone.localtime(start).strftime('%d/%m/%Y · %H:%M') if start else ''
    classified = sum(row['assigned'] or 0 for row in tier_rows)
    active_people = sum(statuses.get(k, {}).get('people', 0) for k in ACTIVE)
    return {
        'status_rows': statuses,
        'confirmed_people': statuses.get(Reservation.Status.CONFIRMED, {}).get('people', 0),
        'pending_people': statuses.get(Reservation.Status.PENDING, {}).get('people', 0),
        'cancelled_people': statuses.get(Reservation.Status.CANCELLED, {}).get('people', 0),
        'no_show_people': statuses.get(Reservation.Status.NO_SHOW, {}).get('people', 0),
        'admitted_people': admitted,
        'guaranteed_people': guarantees['people'] or 0,
        'guaranteed_bookings': guarantees['bookings'] or 0,
        'door_people': door['people'] or 0,
        'door_bookings': door['bookings'] or 0,
        'tier_rows': tier_rows,
        'classified_people': classified,
        'unclassified_people': max(active_people - classified, 0),
        'booking_face_value_syp': sum(row['face_value'] or 0 for row in tier_rows),
        'total_active_people': active_people,
    }
