"""Ticket-only event arrival tracking.

Uses an absolute arrived-count assignment for idempotent retries; does not
create a HubVisit, a restaurant account or a payment.
"""
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog
from events.models import Event
from .models import EventAdmission, Reservation


DAMASCUS = ZoneInfo('Asia/Damascus')


@transaction.atomic
def set_event_admission_total(reservation_id, arrived_count, *, actor):
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError({'actor': 'ليست لديك صلاحية تسجيل حضور الفعاليات.'})
    if isinstance(arrived_count, bool):
        raise ValidationError({'arrived_count': 'عدد الواصلين غير صالح.'})
    try:
        quantity = int(arrived_count)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError({'arrived_count': 'عدد الواصلين يجب أن يكون رقماً صحيحاً.'})
    if str(arrived_count).strip() != str(quantity) or quantity <= 0:
        raise ValidationError({'arrived_count': 'عدد الواصلين غير صالح.'})

    booking = Reservation.objects.select_for_update().select_related('event').get(pk=reservation_id)
    if booking.reservation_type != Reservation.ReservationType.EVENT or not booking.event_id:
        raise ValidationError({'reservation': 'الحجز غير مرتبط بفعالية.'})
    if booking.status != Reservation.Status.CONFIRMED or booking.visit_id:
        raise ValidationError({'status': 'يلزم حجز مؤكد دون جلسة سابقة لتسجيل دخول التذاكر.'})
    if booking.event.status in {Event.Status.CANCELLED, Event.Status.COMPLETED}:
        raise ValidationError({'event': 'لا يمكن تسجيل حضور فعالية ملغاة أو منتهية.'})
    if timezone.localtime(booking.event.starts_at, DAMASCUS).date() != timezone.localtime(timezone.now(), DAMASCUS).date():
        raise ValidationError({'event': 'تسجيل الدخول متاح فقط في تاريخ الفعالية بتوقيت دمشق.'})
    if quantity > booking.party_size:
        raise ValidationError({'arrived_count': 'عدد الواصلين يتجاوز عدد الضيوف في الحجز.'})

    admission = EventAdmission.objects.select_for_update().filter(reservation=booking).first()
    before = admission.arrived_count if admission else 0
    if quantity < before:
        raise ValidationError({'arrived_count': 'لا يمكن إنقاص عدد الواصلين. اطلب تصحيحاً إدارياً.'})
    if quantity == before:
        return admission, False  # Exact repeat is safe and does not log another arrival.
    admission = admission or EventAdmission(reservation=booking)
    admission.arrived_count = quantity
    admission.recorded_by = actor
    admission.full_clean()
    admission.save()

    ActivityLog.objects.create(
        actor=actor,
        action='event.admission.updated',
        details={
            'reservation_id': booking.pk,
            'event_id': booking.event_id,
            'before_count': before,
            'after_count': quantity,
        },
    )
    return admission, True
