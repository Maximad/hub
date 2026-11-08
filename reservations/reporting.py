"""Event operations report built from authoritative reservation and finance records.

No customer contacts are returned. Only posted, unreversed event-ticket cash
receipts attributed to bookings count as collected money. Direct event expenses
have no authorized event-specific attribution here; do not claim net profit.
"""
from collections import defaultdict
from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum, Q
from django.utils import timezone
from core.models import Payment, PostingBatch
from .models import (
    Reservation, ReservationTicketAllocation, ReservationTicketPaymentLink,
    EventAdmission, ReservationPaymentArrangement,
)


def event_operations_report(event):
    bookings = Reservation.objects.filter(
        event=event, reservation_type=Reservation.ReservationType.EVENT,
    )
    counts = {}
    for status in Reservation.Status.values:
        counts[status] = bookings.filter(status=status).aggregate(
            rows=Sum('party_size'),
        )['rows'] or 0
    active = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED,
              Reservation.Status.COMPLETED, Reservation.Status.NO_SHOW)
    allocations = list(
        ReservationTicketAllocation.objects.filter(
            reservation__event=event,
            reservation__status__in=active,
            reservation__reservation_type=Reservation.ReservationType.EVENT,
        ).select_related('ticket_type')
    )
    tiers = {}
    for a in allocations:
        row = tiers.setdefault(a.ticket_type_id, {
            'name': a.ticket_type.name_ar,
            'tickets': 0,
            'nominal': 0,
        })
        row['tickets'] += a.quantity
        row['nominal'] += a.quantity * a.unit_price_syp

    attributed_bookings = {
        a.reservation_id for a in allocations
    }
    unclassified = bookings.filter(status__in=active).exclude(
        pk__in=attributed_bookings,
    ).aggregate(people=Sum('party_size'))['people'] or 0
    arrived = EventAdmission.objects.filter(reservation__event=event).aggregate(
        total=Sum('arrived_count'),
    )['total'] or 0

    links = list(ReservationTicketPaymentLink.objects.filter(
        reservation__event=event,
    ).select_related('payment'))
    payment_ids = [str(row.payment_id) for row in links]
    journals = defaultdict(list)
    if payment_ids:
        content_type = ContentType.objects.get_for_model(Payment)
        for batch in PostingBatch.objects.filter(
            operation_type='order_payment.collect',
            source_content_type=content_type,
            source_object_id__in=payment_ids,
            status=PostingBatch.Status.POSTED,
            reversal_of__isnull=True,
        ).prefetch_related('entries'):
            journals[batch.source_object_id].append(batch)
    posted = 0
    refunded_or_unverified = 0
    for link in links:
        payment = link.payment
        batches = journals.get(str(payment.pk), [])
        if (payment.is_active and not payment.is_reversed
                and payment.method in (Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER)
                and len(batches) == 1 and batches[0].is_balanced()):
            posted += payment.amount_syp
        else:
            refunded_or_unverified += 1

    due_guarantees = ReservationPaymentArrangement.objects.filter(
        reservation__event=event,
        reservation__status__in=(Reservation.Status.PENDING, Reservation.Status.CONFIRMED),
        mode=ReservationPaymentArrangement.Mode.STAFF_GUARANTEE,
        due_at__isnull=False, due_at__lt=timezone.now(),
    ).count()
    return {
        'statuses': counts,
        'arrived': arrived,
        'tier_rows': sorted(tiers.values(), key=lambda x: x['name']),
        'tier_total': sum(row['tickets'] for row in tiers.values()),
        'nominal_total': sum(row['nominal'] for row in tiers.values()),
        'unclassified': unclassified,
        'posted_total': posted,
        'unverified_payment_links': refunded_or_unverified,
        'overdue_guarantees': due_guarantees,
        'profit_available': False,
        'profit_note': 'لا توجد نسبة تكاليف معتمدة لكل فعالية. الربحية غير قابلة للحساب حتى تُنسب المصاريف والأجور فعلياً.',
    }
