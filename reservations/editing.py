"""Authorized editing of event bookings without changing payment or admission state."""
import re

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.permissions import user_has_capability
from core.models import ActivityLog
from events.models import Event
from .models import EventAdmission, Reservation, ReservationTicketOrder, ReservationTicketPaymentLink
from .services import event_reserved_party_size
from .ticketing import set_event_ticket_allocations


_PHONE = re.compile(r'^[+0-9٠-٩۰-۹()\-\s]{7,30}$')


@transaction.atomic
def edit_event_reservation(reservation_id, *, actor, name, phone, party_size,
                           notes='', tier_quantities=None):
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError({'actor': 'لا تملك صلاحية تعديل الحجز.'})
    booking = Reservation.objects.select_for_update(of=('self',)).get(pk=reservation_id)
    if booking.reservation_type != Reservation.ReservationType.EVENT or not booking.event_id:
        raise ValidationError({'event': 'هذا النموذج خاص بحجوزات الفعاليات.'})
    if booking.status not in {Reservation.Status.PENDING, Reservation.Status.CONFIRMED}:
        raise ValidationError({'status': 'لا يمكن تعديل حجز ملغى أو منتهٍ أو غائب.'})
    if booking.visit_id or EventAdmission.objects.filter(reservation=booking).exists():
        raise ValidationError({'status': 'لا يمكن تعديل تفاصيل الحجز بعد تسجيل الوصول.'})
    event = Event.objects.select_for_update().get(pk=booking.event_id)
    if event.status in {Event.Status.CANCELLED, Event.Status.COMPLETED}:
        raise ValidationError({'event': 'لا يمكن تعديل حجز فعالية مغلقة.'})
    name = str(name or '').strip()
    phone = str(phone or '').strip()
    notes = str(notes or '').strip()
    if not 1 <= len(name) <= 120 or any(ord(ch) < 32 for ch in name):
        raise ValidationError({'name': 'اسم صاحب الحجز غير صالح.'})
    if not _PHONE.fullmatch(phone):
        raise ValidationError({'phone': 'رقم التواصل غير صالح.'})
    if len(notes) > 2000:
        raise ValidationError({'notes': 'الملاحظات طويلة جداً.'})
    if isinstance(party_size, bool):
        raise ValidationError({'party_size': 'عدد الضيوف غير صالح.'})
    try:
        size = int(party_size)
    except (ValueError, TypeError, OverflowError):
        raise ValidationError({'party_size': 'عدد الضيوف غير صالح.'})
    if str(party_size).strip() != str(size) or size < 1 or size > 100:
        raise ValidationError({'party_size': 'عدد الضيوف يجب أن يكون بين 1 و100.'})

    old_allocations = {
        row.ticket_type_id: row.quantity for row in booking.ticket_allocations.all()
    }
    new_allocations = old_allocations
    if tier_quantities is not None:
        if not isinstance(tier_quantities, dict):
            raise ValidationError({'tickets': 'فئات التذاكر غير صالحة.'})
        new_allocations = {}
        for raw_key, raw_value in tier_quantities.items():
            if isinstance(raw_key, bool) or isinstance(raw_value, bool):
                raise ValidationError({'tickets': 'فئات التذاكر غير صالحة.'})
            try:
                tier_id, amount = int(raw_key), int(raw_value)
            except (ValueError, TypeError, OverflowError):
                raise ValidationError({'tickets': 'فئات التذاكر غير صالحة.'})
            if (str(raw_key).strip() != str(tier_id)
                    or str(raw_value).strip() != str(amount) or amount < 0):
                raise ValidationError({'tickets': 'عدد فئة التذاكر غير صالح.'})
            if amount:
                new_allocations[tier_id] = amount
    changed_tickets = new_allocations != old_allocations
    changed_size = size != booking.party_size
    if (changed_size or changed_tickets) and ReservationTicketOrder.objects.filter(reservation=booking).exists():
        raise ValidationError({'tickets': 'صدرت فاتورة التذاكر. صحّحها مالياً قبل تغيير المقاعد.'})
    if (changed_size or changed_tickets) and ReservationTicketPaymentLink.objects.filter(
        reservation=booking,
    ).exists():
        raise ValidationError({'tickets': 'لا يمكن تعديل المقاعد بعد ربط سند قبض بالحجز. راجع الإدارة المالية.'})
    if old_allocations and tier_quantities is None and changed_size:
        raise ValidationError({'tickets': 'غيّر توزيع فئات التذاكر مع عدد الأشخاص.'})
    if (old_allocations or new_allocations) and sum(new_allocations.values()) != size:
        raise ValidationError({'tickets': 'مجموع فئات التذاكر يجب أن يساوي عدد الضيوف.'})
    if event.capacity is not None and event_reserved_party_size(
        event.pk, exclude_reservation_id=booking.pk,
    ) + size > event.capacity:
        raise ValidationError({'party_size': 'عدد الضيوف يتجاوز السعة المتبقية.'})

    old_size = booking.party_size
    changed_contact = booking.name != name or booking.phone != phone
    booking.name, booking.phone, booking.party_size, booking.notes = name, phone, size, notes
    booking.full_clean()
    booking.save(update_fields=['name', 'phone', 'party_size', 'notes', 'updated_at'])
    if new_allocations and (changed_tickets or changed_size):
        set_event_ticket_allocations(booking.pk, new_allocations, actor=actor)
    ActivityLog.objects.create(
        actor=actor,
        action='reservation.event_edited',
        details={
            'reservation_id': booking.pk,
            'event_id': event.pk,
            'old_party_size': old_size,
            'new_party_size': size,
            'ticket_quantities_changed': changed_tickets,
            'contact_changed': changed_contact,
        },
    )
    return booking
