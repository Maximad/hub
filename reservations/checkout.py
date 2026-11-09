"""Event ticket checkout using the existing POS and finance posting engine.

Never books a drink SKU: only a hidden event-ticket SKU is used for the sale.
The authoritative receipt is the posted Payment/PostingBatch, not a booking note.
"""
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import is_owner_or_admin, user_has_capability
from core.models import ActivityLog, Category, Order, OrderItem, Payment, Product
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect as collect_order_payment
from events.models import Event, EventTicketType
from .finance_links import link_posted_ticket_payment
from .models import (
    EventAdmission, Reservation, ReservationTicketOrder, ReservationTicketPaymentLink,
)
from .ticketing import validate_saved_ticket_allocations


def _hidden_ticket_product(tier):
    """Serialize by event lock; never select or generate an included beverage."""
    if tier.product_id:
        product = tier.product
        if (product.product_type != Product.ProductType.EVENT
                or product.item_type != Product.ItemType.EVENT_TICKET
                or product.vendor_id or product.requires_preparation):
            raise ValidationError({'tickets': 'منتج فئة التذاكر ليس تذكرة فعالية قابلة للبيع.'})
        return product
    category = Category.objects.filter(name_ar='تذاكر الفعاليات').order_by('pk').first()
    if category is None:
        category = Category.objects.create(name_ar='تذاكر الفعاليات', name_en='Event tickets')
    product = Product.objects.create(
        category=category, name_ar=f'تذكرة: {tier.name_ar}'[:120],
        price_syp=tier.price_syp, item_type=Product.ItemType.EVENT_TICKET,
        product_type=Product.ProductType.EVENT,
        is_available=True, visible_on_pos=False, orderable_on_pos=False,
        visible_on_qr=False, orderable_on_qr=False,
        requires_preparation=False, available_for_events=True,
        available_for_takeaway=False, not_discountable=True,
    )
    tier.product = product
    tier.save(update_fields=['product', 'updated_at'])
    return product


@transaction.atomic
def issue_ticket_order(reservation_id, *, actor):
    """One exact-ticket cashier order per booking; no money is taken here."""
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError({'actor': 'تحتاج إلى صلاحية الحجوزات لإصدار فاتورة تذاكر.'})
    booking = Reservation.objects.select_for_update().get(pk=reservation_id)
    if booking.reservation_type != Reservation.ReservationType.EVENT or not booking.event_id:
        raise ValidationError({'reservation': 'يجب اختيار حجز فعالية.'})
    existing = ReservationTicketOrder.objects.select_related('order').filter(reservation=booking).first()
    if existing:
        return existing, False
    if ReservationTicketPaymentLink.objects.filter(reservation=booking).exists():
        raise ValidationError({'payment': 'توجد سندات قبض مرتبطة بهذا الحجز. تجنّب إصدار فاتورة مكررة.'})
    if booking.status != Reservation.Status.CONFIRMED or booking.visit_id:
        raise ValidationError({'status': 'أكّد الحجز قبل إصدار الفاتورة، ودون جلسة سابقة.'})
    if EventAdmission.objects.filter(reservation=booking).exists():
        raise ValidationError({'status': 'لا يمكن إصدار فاتورة جديدة بعد تسجيل الوصول.'})
    event = Event.objects.select_for_update().get(pk=booking.event_id)
    if event.status in (Event.Status.CANCELLED, Event.Status.COMPLETED):
        raise ValidationError({'event': 'الفعالية مغلقة.'})
    allocations = list(
        booking.ticket_allocations.select_related('ticket_type', 'ticket_type__product')
        .order_by('ticket_type_id')
    )
    if not allocations or sum(row.quantity for row in allocations) != booking.party_size:
        raise ValidationError({'tickets': 'وزّع جميع الضيوف على فئات التذاكر أولاً.'})
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
        product = _hidden_ticket_product(tier)
        line_total = allocation.quantity * allocation.unit_price_syp
        total += line_total
        OrderItem.objects.create(
            order=order, product=product, quantity=allocation.quantity,
            product_name_ar_snapshot=f'{event.title_ar} — {tier.name_ar}'[:120],
            unit_price_syp_snapshot=allocation.unit_price_syp,
            line_total_syp_snapshot=line_total,
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
    sale = ReservationTicketOrder.objects.create(
        reservation=booking, order=order, face_total_syp=total,
        created_by=actor,
    )
    ActivityLog.objects.create(
        actor=actor, action='event.ticket_order.issued',
        details={'reservation_id': booking.pk, 'event_id': event.pk,
                 'order_id': order.pk, 'amount_syp': total},
    )
    return sale, True


@transaction.atomic
def collect_ticket_payment(reservation_id, *, actor, amount_syp, method, request_key):
    """Collect once in the trusted POS journal, then bind receipt in same tx."""
    if not getattr(actor, 'is_active', False) or not is_owner_or_admin(actor) or not user_has_capability(actor, 'cashier'):
        raise ValidationError({'actor': 'قبض تذاكر الفعاليات يحتاج صلاحية الإدارة والكاشير.'})
    if isinstance(amount_syp, bool) or not re.fullmatch(r'[1-9][0-9]{0,11}', str(amount_syp).strip()):
        raise ValidationError({'amount_syp': 'المبلغ يجب أن يكون عدداً موجباً.'})
    amount = int(amount_syp)
    if method not in (Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER):
        raise ValidationError({'method': 'استخدم النقد أو التحويل المسجّل فقط.'})
    key = str(request_key or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9:_\-]{8,100}', key):
        raise ValidationError({'request_key': 'رمز المحاولة غير صالح.'})
    booking = Reservation.objects.select_for_update().get(pk=reservation_id)
    if booking.status != Reservation.Status.CONFIRMED or booking.reservation_type != Reservation.ReservationType.EVENT:
        raise ValidationError({'status': 'القبض متاح لحجز فعالية مؤكد فقط.'})
    sale = ReservationTicketOrder.objects.select_related('order').filter(reservation=booking).first()
    if sale is None:
        raise ValidationError({'order': 'يجب إصدار فاتورة التذاكر أولاً.'})
    if sale.order.status == Order.Status.CANCELLED:
        raise ValidationError({'order': 'الفاتورة ملغاة.'})
    context = PostingContext(
        actor=actor, business_date=timezone.localdate(),
        channel='event-ticket', idempotency_key=f'event-ticket:{sale.pk}:{key}',
        request_metadata={'reservation_id': booking.pk, 'event_id': booking.event_id},
    )
    # finance service owns the actual receipt, period gates and idempotency.
    payment = collect_order_payment(sale.order, context, amount, method)
    # If linkage fails, transaction rolls back both Payment and its journal.
    link_posted_ticket_payment(booking.pk, payment.pk, actor=actor)
    return payment
