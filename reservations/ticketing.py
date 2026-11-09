"""Authoritative event ticket allocation service.

Ticket allocations record attendance by tier and price at booking time, NOT
receipts. The event row serializes booking allocation and status transitions.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounts.permissions import user_has_capability
from core.models import ActivityLog
from events.models import Event, EventTicketType
from .models import Reservation, ReservationTicketAllocation, ReservationTicketOrder

_ACTIVE = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED)


def _validated_quantities(quantities):
    if not isinstance(quantities, dict):
        raise ValidationError({'tickets': 'أدخل فئات التذاكر وأعدادها.'})
    result = {}
    for raw_id, raw_quantity in quantities.items():
        if isinstance(raw_id, bool) or isinstance(raw_quantity, bool):
            raise ValidationError({'tickets': 'بيانات فئات التذاكر غير صالحة.'})
        try:
            ticket_id = int(raw_id)
            quantity = int(raw_quantity)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError({'tickets': 'أعداد التذاكر يجب أن تكون أعداداً صحيحة.'})
        if (str(raw_quantity).strip() != str(quantity)
                or ticket_id <= 0 or quantity < 0 or quantity > 100000):
            raise ValidationError({'tickets': 'عدد التذاكر غير صالح.'})
        if quantity:
            if ticket_id in result:
                raise ValidationError({'tickets': 'فئة التذكرة مكررة.'})
            result[ticket_id] = quantity
    return result


def _check_tier_usage(event, reservation, quantities):
    if quantities and sum(quantities.values()) != reservation.party_size:
        raise ValidationError({'party_size': 'مجموع التذاكر يجب أن يساوي عدد ضيوف الحجز.'})
    tickets = EventTicketType.objects.filter(event=event, pk__in=quantities)
    available = {row.pk: row for row in tickets}
    if len(available) != len(quantities):
        raise ValidationError({'tickets': 'تحتوي البيانات على فئة تذكرة لا تتبع الفعالية.'})
    from .services import event_reserved_party_size
    if reservation.status in _ACTIVE and event.capacity is not None:
        used = event_reserved_party_size(event.pk, exclude_reservation_id=reservation.pk)
        if used + reservation.party_size > event.capacity:
            raise ValidationError({'party_size': 'عدد الضيوف يتجاوز سعة الفعالية.'})
    if reservation.status in _ACTIVE:
        for ticket_id, quantity in quantities.items():
            ticket = available[ticket_id]
            if ticket.capacity is None:
                continue
            assigned = (
                ReservationTicketAllocation.objects.filter(
                    ticket_type_id=ticket_id,
                    reservation__event_id=event.pk,
                    reservation__status__in=_ACTIVE,
                    reservation__reservation_type=Reservation.ReservationType.EVENT,
                ).exclude(reservation_id=reservation.pk)
                .aggregate(total=Sum('quantity'))['total'] or 0
            )
            if assigned + quantity > ticket.capacity:
                raise ValidationError({
                    'tickets': f'فئة «{ticket.name_ar}» لا تتسع لعدد التذاكر المطلوب.',
                })
    return available


def validate_saved_ticket_allocations(reservation, *, event=None):
    """Run while the reservation's Event row is locked by the status service."""
    if not reservation.pk:
        return
    existing = list(reservation.ticket_allocations.all())
    if not existing:
        # Legacy/current public approvals can have an unclassified reservation.
        return
    event = event or reservation.event
    quantities = {row.ticket_type_id: row.quantity for row in existing}
    _check_tier_usage(event, reservation, quantities)


@transaction.atomic
def set_event_ticket_allocations(reservation_id, quantities, *, actor):
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError({'tickets': 'لا تملك صلاحية تعديل فئات الحجوزات.'})
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    if reservation.reservation_type != Reservation.ReservationType.EVENT or not reservation.event_id:
        raise ValidationError({'tickets': 'هذا الحجز غير مرتبط بفعالية.'})
    if reservation.status not in _ACTIVE or reservation.visit_id:
        raise ValidationError({'tickets': 'لا يمكن تعديل فئات حجز منتهٍ أو ملغى أو بعد تسجيل الوصول.'})
    if ReservationTicketOrder.objects.filter(reservation=reservation).exists():
        raise ValidationError({'tickets': 'صدرت فاتورة التذاكر؛ لا يمكن تغيير الفئات بعد إصدارها.'})
    if reservation.ticket_payment_links.exists():
        raise ValidationError({'tickets': 'هذا الحجز مرتبط بسند قبض. يلزم تسوية مالية قبل تعديل فئات التذاكر.'})

    # Lock-order matches existing reservation confirmation: booking row, then event.
    event = Event.objects.select_for_update().get(pk=reservation.event_id)
    quantities = _validated_quantities(quantities)
    if not quantities:
        raise ValidationError({'tickets': 'حدد فئة تذكرة واحدة على الأقل.'})
    available = _check_tier_usage(event, reservation, quantities)
    existing = {
        row.ticket_type_id: row
        for row in ReservationTicketAllocation.objects.select_for_update().filter(reservation=reservation)
    }
    for ticket_id in quantities:
        if not available[ticket_id].is_active and ticket_id not in existing:
            raise ValidationError({'tickets': 'لا يمكن حجز فئة تذاكر غير نشطة.'})

    before = {str(tid): row.quantity for tid, row in existing.items()}
    for ticket_id, row in existing.items():
        if ticket_id not in quantities:
            row.delete()
        elif row.quantity != quantities[ticket_id]:
            row.quantity = quantities[ticket_id]
            row.full_clean()
            row.save(update_fields=['quantity', 'updated_at'])
    for ticket_id, quantity in quantities.items():
        if ticket_id not in existing:
            row = ReservationTicketAllocation(
                reservation=reservation,
                ticket_type=available[ticket_id],
                quantity=quantity,
                unit_price_syp=available[ticket_id].price_syp,
            )
            row.full_clean()
            row.save()
    ActivityLog.objects.create(
        actor=actor, action='reservation.ticket_allocations.changed',
        details={
            'reservation_id': reservation.pk,
            'event_id': event.pk,
            'before': before,
            'after': {str(k): v for k, v in quantities.items()},
        },
    )
    return reservation
