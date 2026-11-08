"""Create one event-ticket-only order, then use the EXISTING cashier to collect.

There is no drink selection here. Internal event-ticket catalog SKUs are an
implementation detail needed by the current OrderItem/POS posting engine.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.permissions import is_owner_or_admin, user_has_capability
from core.models import ActivityLog, Category, Order, OrderItem, Product
from events.models import Event, EventTicketType
from .models import Reservation, ReservationTicketSale


def _ticket_product(tier, category, booking_price):
    """Reuse/prepare an internal event-ticket SKU; never a beverage or customer menu item."""
    product = tier.product
    if product:
        if product.item_type != Product.ItemType.EVENT_TICKET or product.product_type != Product.ProductType.EVENT:
            raise ValidationError({'tickets': 'المنتج المرتبط بالفئة ليس منتج تذاكر صالحاً. لا يمكن استخدام مشروب بدلاً من التذكرة.'})
        return product

    product = (
        Product.objects.filter(
            metadata__hub_event_ticket_tier_id=tier.pk,
            item_type=Product.ItemType.EVENT_TICKET,
            product_type=Product.ProductType.EVENT,
        ).order_by('pk').first()
    )
    if not product:
        product = Product.objects.create(
            category=category,
            name_ar=f'تذكرة {tier.event.title_ar} — {tier.name_ar}'[:120],
            price_syp=booking_price,
            item_type=Product.ItemType.EVENT_TICKET,
            product_type=Product.ProductType.EVENT,
            is_available=True,
            requires_preparation=False,
            visible_on_pos=False,
            orderable_on_pos=False,
            visible_on_qr=False,
            orderable_on_qr=False,
            metadata={'hub_event_ticket_tier_id': tier.pk, 'hub_event_id': tier.event_id},
        )
    tier.product = product
    tier.save(update_fields=['product'])
    return product


@transaction.atomic
def create_event_ticket_order(reservation_id, *, actor):
    if not getattr(actor, 'is_active', False) or not (
        is_owner_or_admin(actor)
        or (user_has_capability(actor, 'reservations') and user_has_capability(actor, 'cashier'))
    ):
        raise ValidationError({'actor': 'إنشاء طلب تذاكر يتطلب صلاحية الحجز والكاشير.'})
    booking = Reservation.objects.select_for_update(of=('self',)).select_related('event').get(pk=reservation_id)
    if booking.reservation_type != Reservation.ReservationType.EVENT or not booking.event_id:
        raise ValidationError({'reservation': 'الحجز غير مرتبط بفعالية.'})
    if booking.status != Reservation.Status.CONFIRMED:
        raise ValidationError({'status': 'يجب تأكيد الحجز قبل إصدار طلب التذاكر.'})

    event = Event.objects.select_for_update().get(pk=booking.event_id)
    from .ticketing import validate_saved_ticket_allocations
    validate_saved_ticket_allocations(booking, event=event)
    existing = ReservationTicketSale.objects.select_related('order').filter(
        reservation=booking,
    ).first()
    if existing:
        return existing, False

    allocations = list(
        booking.ticket_allocations.select_related('ticket_type', 'ticket_type__product')
        .order_by('ticket_type_id')
    )
    if not allocations or sum(a.quantity for a in allocations) != booking.party_size:
        raise ValidationError({'tickets': 'يجب توزيع جميع تذاكر الحجز قبل إنشاء طلب الكاشير.'})
    if event.status == Event.Status.CANCELLED:
        raise ValidationError({'event': 'الفعالية ملغاة.'})
    if any(a.ticket_type.event_id != event.pk for a in allocations):
        raise ValidationError({'tickets': 'هناك تذاكر من فعالية أخرى.'})

    category = Category.objects.filter(name_ar='تذاكر الفعاليات').order_by('pk').first()
    if category is None:
        category = Category.objects.create(name_ar='تذاكر الفعاليات', name_en='Event tickets')
    order = Order.objects.create(
        status=Order.Status.ACCEPTED,
        entry_source=Order.EntrySource.STAFF_POS,
        entered_by=actor,
        service_mode=Order.ServiceMode.DINE_IN,
        fulfillment_mode=Order.FulfillmentMode.INSIDE_SPACE,
        notes=f'طلب تذاكر فعالية #{event.pk} / حجز #{booking.pk}',
    )
    for allocation in allocations:
        product = _ticket_product(
            allocation.ticket_type, category, allocation.unit_price_syp,
        )
        OrderItem.objects.create(
            order=order, product=product,
            quantity=allocation.quantity,
            product_name_ar_snapshot=allocation.ticket_type.name_ar,
            unit_price_syp_snapshot=allocation.unit_price_syp,
            line_total_syp_snapshot=allocation.quantity * allocation.unit_price_syp,
            selected_options_snapshot=[],
            prep_status=OrderItem.PrepStatus.NO_PREP,
            item_note=f'فئة تذكرة #{allocation.ticket_type_id}',
        )
    ticket_sale = ReservationTicketSale.objects.create(
        reservation=booking, order=order, created_by=actor,
    )
    ActivityLog.objects.create(
        actor=actor, action='reservation.ticket_sale.created',
        details={
            'reservation_id': booking.pk,
            'event_id': event.pk,
            'order_id': order.pk,
            'nominal_amount_syp': order.total_syp,
        },
    )
    return ticket_sale, True
