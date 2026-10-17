"""Classify POS items as Hub hospitality or owner personal consumption.

Never create cash payments, manual stock movements or accounting postings.
The standard order/recipe flow remains responsible for inventory consumption.
"""
from decimal import Decimal
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import can_approve_partial_payment
from core.models import (
    ActivityLog, AuditEvent, HubVisit, Order, OrderConsumption,
    OrderDiscount, OrderItem, Payment,
)


def can_record_consumption(actor, category):
    if not actor or not actor.is_authenticated or not actor.is_active:
        return False
    if category == OrderConsumption.Category.OWNER_PERSONAL:
        return bool(actor.is_superuser)
    return category == OrderConsumption.Category.HUB_HOSPITALITY and can_approve_partial_payment(actor)


def _quantity(raw):
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationError('اختر كمية صحيحة وموجبة.') from exc
    if str(value) != str(raw).strip() or value <= 0 or value > 999:
        raise ValidationError('اختر كمية صحيحة وموجبة لا تتجاوز 999.')
    return value


def record_consumption(*, actor, order_id, order_item_id, category, quantity,
                       reason, request_key, owner_treatment='', beneficiary_note=''):
    if not can_record_consumption(actor, category):
        raise PermissionDenied('هذا النوع من الضيافة أو الاستهلاك يحتاج موافقة المستخدم المخوّل.')
    try:
        request_key = str(uuid.UUID(str(request_key)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError('رمز تسجيل العملية غير صالح. حدّث الصفحة وحاول مجدداً.') from exc
    amount_qty = _quantity(quantity)
    reason = str(reason or '').strip()
    if not reason or len(reason) > 255:
        raise ValidationError('أدخل سبباً موجزاً وواضحاً للاستهلاك.')
    beneficiary_note = str(beneficiary_note or '').strip()
    if len(beneficiary_note) > 120:
        raise ValidationError('ملاحظة المستفيد طويلة جداً.')
    if category == OrderConsumption.Category.OWNER_PERSONAL:
        if owner_treatment not in OrderConsumption.OwnerTreatment.values:
            raise ValidationError('اختر طريقة التسوية الشخصية المقترحة.')
    elif owner_treatment:
        raise ValidationError('طريقة التسوية الشخصية تخص المالك فقط.')

    # Serialize classification, order pricing and payment-state review under one
    # order lock. Duplicated form submissions must return the original result.
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        previous = OrderConsumption.objects.filter(request_key=request_key).first()
        if previous:
            if (previous.order_id == order.pk and previous.order_item_id == order_item_id
                    and previous.created_by_id == actor.pk
                    and previous.category == category and previous.quantity == amount_qty):
                return previous, False
            raise ValidationError('رمز العملية مستخدم مع طلب مختلف. حدّث الصفحة.')
        if order.status == Order.Status.CANCELLED:
            raise ValidationError('لا يمكن تقديم ضيافة لطلب ملغى.')
        if order.visit_id and order.visit.status != HubVisit.Status.OPEN:
            raise ValidationError('حساب الجلسة مغلق. لا يمكن تعديل المبالغ بعد إغلاقه.')
        if order.payments.filter(is_active=True, is_reversed=False).exclude(method=Payment.Method.UNPAID).exists():
            raise ValidationError('تم تسجيل دفعة على الطلب. لا يمكن إضافة ضيافة بعد التحصيل في هذا الإصدار.')
        order_item = OrderItem.objects.select_for_update().select_related('product').filter(
            pk=order_item_id, order_id=order.pk
        ).first()
        if not order_item:
            raise ValidationError('المنتج المحدد غير موجود في الطلب.')
        product = order_item.product
        if (order_item.prep_status == OrderItem.PrepStatus.CANCELLED
                or product.not_discountable
                or product.item_type not in {product.ItemType.FOOD, product.ItemType.BEVERAGE, product.ItemType.RETAIL}):
            raise ValidationError('هذا المنتج لا يقبل الضيافة أو الاستهلاك الداخلي.')
        previous_qty = sum(
            c.quantity for c in OrderConsumption.objects.filter(order_item=order_item)
        )
        if previous_qty + amount_qty > order_item.quantity:
            raise ValidationError('الكمية المختارة تتجاوز الكمية غير المصنفة من المنتج.')
        # Existing unclassified discounts cannot safely be attributed to a
        # particular line; refuse rather than granting the same item twice.
        classified_ids = OrderConsumption.objects.filter(order=order).values_list('discount_id', flat=True)
        if order.discounts.filter(is_active=True).exclude(pk__in=classified_ids).exists():
            raise ValidationError('يوجد خصم آخر على الطلب. راجعه أولاً قبل تصنيف الضيافة.')
        line_total = order_item.line_total_syp_snapshot
        if line_total is None:
            line_total = order_item.unit_price_syp_snapshot * order_item.quantity
        if (line_total <= 0 or line_total % order_item.quantity != 0):
            raise ValidationError('سعر هذا السطر غير قابل للتقسيم بدقة. يلزم تصحيح السعر قبل الضيافة.')
        each_price = line_total // order_item.quantity
        amount_syp = each_price * amount_qty
        if amount_syp <= 0 or amount_syp > order.remaining_syp:
            raise ValidationError('قيمة الضيافة تتجاوز الرصيد المتبقي للطلب.')
        unit_cost = order_item.estimated_unit_cost_syp_snapshot
        if unit_cost is None:
            unit_cost = product.estimated_unit_cost_syp
        if unit_cost is None:
            unit_cost = product.cost_syp
        estimated_cost = (Decimal(unit_cost) * amount_qty).quantize(Decimal('0.01')) if unit_cost is not None else None
        discount = OrderDiscount(
            order=order, discount_type=OrderDiscount.DiscountType.COMP,
            amount_syp=amount_syp,
            reason='ضيافة هَب' if category == OrderConsumption.Category.HUB_HOSPITALITY else 'استخدام شخصي للمالك',
            notes=reason, created_by=actor, approved_by=actor,
        )
        discount.full_clean()
        discount.save()
        consumption = OrderConsumption(
            order=order, order_item=order_item, discount=discount,
            category=category, owner_treatment=owner_treatment, quantity=amount_qty,
            retail_value_syp=amount_syp, estimated_cost_syp=estimated_cost,
            business_date=timezone.localdate(), reason=reason,
            beneficiary_note=beneficiary_note, request_key=request_key,
            created_by=actor, approved_by=actor,
        )
        consumption.full_clean()
        consumption.save()
        snapshot = {
            'order_id': order.pk, 'order_item_id': order_item.pk,
            'category': category, 'quantity': amount_qty,
            'retail_value_syp': amount_syp,
            'estimated_cost_syp': str(estimated_cost) if estimated_cost is not None else None,
            'discount_id': discount.pk, 'owner_treatment': owner_treatment,
        }
        AuditEvent.objects.create(
            actor=actor, approver=actor, action='pos_consumption_recorded',
            source_content_type=ContentType.objects.get_for_model(OrderConsumption),
            source_object_id=str(consumption.pk), after_snapshot=snapshot,
            request_key='pos-consumption:' + request_key, channel='staff-cashier',
        )
        ActivityLog.objects.create(
            actor=actor, action='pos_consumption_recorded',
            details={'consumption_id': consumption.pk, **snapshot},
        )
        return consumption, True
