"""Link *already posted* POS ticket payments to event reservations.

This module does not call finance posting or invent a payment. It accepts only
verified active journal-backed receipts for event-ticket-only POS orders.
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.permissions import is_owner_or_admin
from core.models import ActivityLog, Order, Payment, PostingBatch, Product
from .models import Reservation, ReservationTicketPaymentLink


def _verified_payment(payment):
    if (
        not payment.is_active or payment.is_reversed
        or payment.method not in {Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER}
        or payment.amount_syp <= 0
    ):
        return False
    content_type = ContentType.objects.get_for_model(Payment)
    journals = list(PostingBatch.objects.filter(
        operation_type='order_payment.collect',
        source_content_type=content_type,
        source_object_id=str(payment.pk),
        status=PostingBatch.Status.POSTED,
        reversal_of__isnull=True,
    ))
    return len(journals) == 1 and journals[0].is_balanced()


def ticket_payment_summary(reservation):
    allocations = list(reservation.ticket_allocations.all())
    if not allocations:
        return {'classified': False, 'expected': 0, 'posted': 0, 'outstanding': None, 'label': 'لم تُحدّد فئات التذاكر'}
    expected = sum(row.quantity * row.unit_price_syp for row in allocations)
    links = ReservationTicketPaymentLink.objects.filter(
        reservation=reservation,
    ).select_related('payment')
    # Canonical ticket-only orders are linked directly to reservations. Their
    # real cashier payments count automatically once a posting exists.
    linked_payments = {row.payment_id: row.payment for row in links}
    for payment in Payment.objects.filter(
        order__event_ticket_sale__reservation=reservation,
    ):
        linked_payments[payment.pk] = payment
    posted = sum(
        payment.amount_syp
        for payment in linked_payments.values()
        if _verified_payment(payment)
    )
    outstanding = max(expected - posted, 0)
    if expected <= 0:
        label = 'يحتاج إلى مراجعة'
    elif posted == 0:
        label = 'غير مدفوع'
    elif posted >= expected:
        label = 'مدفوع بالكامل'
    else:
        label = 'مدفوع جزئياً'
    return {
        'classified': True, 'expected': expected, 'posted': posted,
        'outstanding': outstanding, 'label': label,
    }


@transaction.atomic
def link_posted_ticket_payment(reservation_id, payment_id, *, actor):
    if not is_owner_or_admin(actor):
        raise ValidationError({'payment': 'ربط سندات قبض الفعاليات يتطلب موافقة الإدارة.'})
    booking = Reservation.objects.select_for_update().get(pk=reservation_id)
    if booking.reservation_type != Reservation.ReservationType.EVENT or not booking.event_id:
        raise ValidationError({'reservation': 'هذا ليس حجز فعالية.'})
    payment = Payment.objects.select_for_update().select_related('order').get(pk=payment_id)

    previous = ReservationTicketPaymentLink.objects.select_for_update().filter(
        payment=payment,
    ).first()
    if previous:
        if previous.reservation_id == booking.pk:
            return previous, False
        raise ValidationError({'payment': 'سند القبض مرتبط بالفعل بحجز آخر.'})
    if payment.order.status == Order.Status.CANCELLED:
        raise ValidationError({'payment': 'طلب البيع ملغى.'})
    if payment.order.event_ticket_sale if hasattr(payment.order, 'event_ticket_sale') else False:
        raise ValidationError({'payment': 'سند طلب التذاكر المرتبط بالحجز يحتسب تلقائياً؛ لا تربطه يدوياً.'})
    if not _verified_payment(payment):
        raise ValidationError({'payment': 'لا يوجد سند قبض مالي نشط ومُرحّل يمكن ربطه.'})
    items = payment.order.items.all()
    if not items.exists() or items.exclude(
        product__item_type=Product.ItemType.EVENT_TICKET,
    ).exists():
        raise ValidationError({'payment': 'يجب أن يحتوي طلب الكاشير على تذاكر فعاليات فقط.'})
    summary = ticket_payment_summary(booking)
    if not summary['classified']:
        raise ValidationError({'tickets': 'حدّد توزيع فئات التذاكر أولاً.'})
    if payment.amount_syp > summary['outstanding']:
        raise ValidationError({'payment': 'مبلغ السند يتجاوز المتبقي على هذا الحجز.'})
    record = ReservationTicketPaymentLink(
        reservation=booking, payment=payment, linked_by=actor,
    )
    record.full_clean()
    record.save()
    ActivityLog.objects.create(
        actor=actor, action='reservation.ticket_payment.linked',
        details={
            'reservation_id': booking.pk,
            'payment_id': payment.pk,
            'order_id': payment.order_id,
            'amount_syp': payment.amount_syp,
        },
    )
    return record, True
