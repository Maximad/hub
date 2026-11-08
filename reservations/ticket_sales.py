"""Create one auditable POS order for the exact ticket allocation of a booking.

This prepares a cashier balance; it does not collect funds. Payment and reversal
must use the existing posting engine through its authorized cashier UI.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.permissions import user_has_capability
from core.models import ActivityLog, Category, Order, OrderItem, Product
from events.models import Event, EventTicketType
from .models import EventAdmission, Reservation, ReservationTicketSale


def _ticket_charge_product(tier):
    product = tier.product
    if product is not None:
        if product.item_type != Product.ItemType.EVENT_TICKET or product.product_type != Product.ProductType.EVENT:
            raise ValidationError({'tickets': 'المنتج المرتبط بفئة التذكرة ليس منتج تذاكر صالحاً.'})
        if product.vendor_id:
            raise ValidationError({'tickets': 'لا يمكن ترحيل تذكرة مرتبطة بمنتج شريك بهذه الطريقة.'})
        return product
    category = Category.objects.filter(name_ar='تذاكر الفعاليات').order_by('pk').first()
    if category is None:
        category = Category.objects.create(name_ar='تذاكر الفعاليات', name_en='Event tickets')
    product = Product.objects.create(
        category=category,
        name_ar=f'تذكرة: {tier.name_ar}'[:120],
        price_syp=tier.price_syp,
        item_type=Product.ItemType.EVENT_TICKET,
        product_type=Product.ProductType.EVENT,
        is_available=True,
        visible_on_pos=False, orderable_on_pos=False,
        visible_on_qr=False, orderable_on_qr=False,
        requires_preparation=False,
        available_for_events=True,
        available_for_takeaway=False,
        not_discountable=True,
    )
    tier.product = product
    tier.save(update_fields=['product', 'updated_at'])
    return product


@transaction.atomic
def ensure_reservation_ticket_order(reservation_id, *, actor):
    """Idempotently create a ticket-charge order. A repeated call never re-sells."""
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError({'actor': 'تحتاج إلى صلاحية إدارة الحجوزات.'})
    booking = Reservation.objects.select_for_update().get(pk=reservation_id)
    if booking.reservation_type != Reservation.ReservationType.EVENT or not booking.event_id:
        raise ValidationError({'reservation': 'هذا ليس حجز فعالية.'})
    existing = ReservationTicketSale.objects.select_related('order').filter(reservation=booking).first()
    if existing:
        return existing, False
    if booking.status != Reservation.Status.CONFIRMED or booking.visit_id:
        raise ValidationError({'status': 'أكّد الحجز قبل إنشاء فاتورة التذاكر، ولا تنشئها بعد بدء جلسة.'})
    if EventAdmission.objects.filter(reservation=booking).exists():
        raise ValidationError({'status': 'لا يمكن إنشاء فاتورة تذاكر جديدة بعد تسجيل الحضور.'})
    event = Event.objects.select_for_update().get(pk=booking.event_id)
    if event.status in (Event.Status.CANCELLED, Event.Status.COMPLETED):
        raise ValidationError({'event': 'الفعالية ملغاة أو منتهية.'})
    allocations = list(
        booking.ticket_allocations.select_related('ticket_type', 'ticket_type__product').order_by('ticket_type_id')
    )
    if not allocations or sum(row.quantity for row in allocations) != booking.party_size:
        raise ValidationError({'tickets': 'وزع جميع تذاكر الحجز على الفئات قبل إنشاء فاتورة.'})
    from .ticketing import validate_saved_ticket_allocations
    validate_saved_ticket_allocations(booking, event=event)

    order = Order.objects.create(
        status=Order.Status.SERVED,
        entry_source=Order.EntrySource.SYSTEM,
        entered_by=actor,
        notes=f'تذاكر فعالية {event.pk} / حجز {booking.pk}',
    )
    total = 0
    for allocation in allocations:
        tier = EventTicketType.objects.select_for_update().get(pk=allocation.ticket_type_id)
        product = _ticket_charge_product(tier)
        line_total = allocation.quantity * allocation.unit_price_syp
        total += line_total
        OrderItem.objects.create(
            order=order, product=product, quantity=allocation.quantity,
            product_name_ar_snapshot=f'{event.title_ar} — {tier.name_ar}'[:120],
            unit_price_syp_snapshot=allocation.unit_price_syp,
            line_total_syp_snapshot=line_total,
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
    sale = ReservationTicketSale.objects.create(
        reservation=booking,
        order=order,
        booked_total_syp=total,
        created_by=actor,
    )
    ActivityLog.objects.create(
        actor=actor,
        action='reservation.ticket_order.created',
        details={
            'reservation_id': booking.pk,
            'event_id': event.pk,
            'order_id': order.pk,
            'booked_total_syp': total,
        },
    )
    return sale, True
