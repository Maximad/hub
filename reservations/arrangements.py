"""Bookkeeping of who authorized an unpaid, confirmed booking.

Does NOT mark a payment as collected. An event ticket sale needs an actual
posted cashier order/receipt, implemented separately.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import is_owner_or_admin, user_has_capability
from core.models import ActivityLog
from .models import Reservation, ReservationPaymentArrangement
from .services import change_reservation_status


@transaction.atomic
def set_payment_arrangement(reservation_id, *, actor, mode, reason='', due_at=None, confirm=False):
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError({'mode': 'ليست لديك صلاحية إدارة الحجوزات.'})
    if mode not in ReservationPaymentArrangement.Mode.values:
        raise ValidationError({'mode': 'طريقة السداد غير صالحة.'})
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    if reservation.visit_id or reservation.status not in {
        Reservation.Status.PENDING, Reservation.Status.CONFIRMED,
    }:
        raise ValidationError({'status': 'لا يمكن تعديل اتفاق السداد بعد الوصول أو إنهاء الحجز.'})
    existing = ReservationPaymentArrangement.objects.select_for_update().filter(
        reservation=reservation,
    ).first()
    if mode == ReservationPaymentArrangement.Mode.STAFF_GUARANTEE:
        if not is_owner_or_admin(actor):
            raise ValidationError({'mode': 'الضمان يتطلب موافقة الإدارة.'})
        if len((reason or '').strip()) < 3:
            raise ValidationError({'reason': 'أدخل سبباً لاعتماد ضمان الحجز.'})
    elif existing and existing.mode == ReservationPaymentArrangement.Mode.STAFF_GUARANTEE:
        if not is_owner_or_admin(actor):
            raise ValidationError({'mode': 'إلغاء الضمان السابق يحتاج إلى موافقة الإدارة.'})
    if due_at is not None and (
        timezone.is_naive(due_at) or due_at <= timezone.now()
    ):
        raise ValidationError({'due_at': 'موعد التسديد يجب أن يكون مستقبلياً.'})
    before = None
    if existing:
        before = {
            'mode': existing.mode,
            'guarantor_id': existing.guaranteed_by_id,
            'due_at': existing.due_at.isoformat() if existing.due_at else None,
        }
    record = existing or ReservationPaymentArrangement(reservation=reservation)
    record.mode = mode
    record.guaranteed_by = (
        actor if mode == ReservationPaymentArrangement.Mode.STAFF_GUARANTEE else None
    )
    record.approved_by = actor
    record.reason = (reason or '').strip() if mode == ReservationPaymentArrangement.Mode.STAFF_GUARANTEE else ''
    record.due_at = due_at
    record.full_clean()
    record.save()

    if confirm and reservation.status == Reservation.Status.PENDING:
        # The existing authoritative booking state machine validates capacity.
        # A failed confirmation rolls back this arrangement too.
        change_reservation_status(reservation.pk, Reservation.Status.CONFIRMED, actor=actor)

    ActivityLog.objects.create(
        actor=actor, action='reservation.payment_arrangement.changed',
        details={
            'reservation_id': reservation.pk,
            'before': before,
            'after': {
                'mode': record.mode,
                'guarantor_id': record.guaranteed_by_id,
                'due_at': record.due_at.isoformat() if record.due_at else None,
                'confirmed_now': bool(confirm),
            },
        },
    )
    return record
