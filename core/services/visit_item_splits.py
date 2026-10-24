"""Restricted, audited split of one unposted item quantity into another open bill.

No payment, inventory, entitlement, or customer browser credential is moved.
All invariants must hold at POST time; interface controls are not authority.
"""
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, HubVisit, Order, OrderItem, Payment, StockMovement
from core.stock_recipes import deduction_settings


@transaction.atomic
def split_unposted_order_item(
    *, actor, source_visit_id, destination_visit_id, order_id, item_id,
    quantity, reason, request_token,
):
    reason = (reason or '').strip()
    if not 3 <= len(reason) <= 255:
        raise ValidationError('سبب التقسيم مطلوب (3 إلى 255 حرفاً).')
    try:
        quantity = int(quantity)
    except (ValueError, TypeError):
        raise ValidationError('أدخل كمية صالحة.')
    try:
        request_token = str(UUID(str(request_token)))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError('انتهت صلاحية نموذج التقسيم. أعد فتح الحساب.')
    if quantity < 1:
        raise ValidationError('الكمية يجب أن تكون واحدة على الأقل.')
    if not source_visit_id or not destination_visit_id or source_visit_id == destination_visit_id:
        raise ValidationError('اختر حساب مصدر وحساب وجهة مختلفين.')

    # Consistent order with existing whole-order transfers prevents inverse
    # direction operations from deadlocking.
    visit_ids = sorted([source_visit_id, destination_visit_id])
    visits = {
        v.pk: v for v in HubVisit.objects.select_for_update()
        .filter(pk__in=visit_ids).order_by('pk')
    }
    if len(visits) != 2 or any(v.status != HubVisit.Status.OPEN for v in visits.values()):
        raise ValidationError('الحساب المصدر والوجهة يجب أن يكونا مفتوحين.')
    destination = visits[destination_visit_id]
    # The source visit lock serializes duplicate posts. A retry with the same
    # token returns its prior result, not a second split.
    previous = ActivityLog.objects.filter(
        action='visit.order_item_split',
        details__idempotency_key=request_token,
    ).order_by('pk').first()
    if previous:
        d = previous.details
        if (
            previous.actor_id != getattr(actor, 'pk', None)
            or d.get('source_visit_id') != source_visit_id
            or d.get('destination_visit_id') != destination_visit_id
            or d.get('source_order_id') != order_id
            or d.get('source_item_id') != item_id
            or d.get('moved_quantity') != quantity
            or d.get('reason') != reason
        ):
            raise ValidationError('مفتاح عملية التقسيم مستخدم لطلب مختلف.')
        return Order.objects.get(pk=d['new_order_id'])

    order = Order.objects.select_for_update(of=('self',)).filter(pk=order_id).first()
    if order is None or order.visit_id != source_visit_id:
        raise ValidationError('الطلب تغيّر منذ فتح الصفحة. أعد تحميل الحساب.')
    _stock_settings, automatic_stock_deduction = deduction_settings()
    if automatic_stock_deduction:
        raise ValidationError('التقسيم غير متاح عند تفعيل خصم المخزون التلقائي. استخدم مساراً مخزونياً معتمداً.')
    if order.status != Order.Status.NEW:
        raise ValidationError('التقسيم متاح فقط قبل استلام الطلب أو بدء التحضير.')
    if order.fulfillment_mode not in {
        Order.FulfillmentMode.TABLE, Order.FulfillmentMode.INSIDE_SPACE,
    }:
        raise ValidationError('طلبات التوصيل والتيك أواي لا تدعم هذا التقسيم.')
    if order.fulfillment_mode == Order.FulfillmentMode.TABLE and not destination.table_id:
        raise ValidationError('هذا الطلب يحتاج طاولة على الحساب الوجهة.')
    if order.member_id and order.member_id != destination.member_id:
        raise ValidationError('الطلب مرتبط بعضو مختلف عن الحساب الوجهة.')

    # A reversal still leaves financial history and must remain blocked.
    if Payment.objects.filter(order=order).exists() or order.discounts.exists():
        raise ValidationError('للطلب سجل دفعات أو خصومات. استخدم مسار التصحيح المالي المتخصص.')
    if order.internet_entitlements.exists() or order.internet_sessions.exists():
        raise ValidationError('الطلب مرتبط بخدمات إنترنت ولا يمكن تقسيمه.')
    if StockMovement.objects.filter(related_order=order).exists():
        raise ValidationError('للطلب حركة مخزون مسجلة. التقسيم يتطلب مراجعة مخزنية.')

    item = OrderItem.objects.select_for_update(of=('self',)).filter(pk=item_id, order=order).first()
    if item is None:
        raise ValidationError('عنصر الطلب تغيّر منذ فتح الصفحة.')
    if item.prep_status not in {
        OrderItem.PrepStatus.NEW, OrderItem.PrepStatus.PENDING,
    }:
        raise ValidationError('بدأ التحضير أو التقديم لهذا العنصر. التقسيم غير متاح.')
    if item.stock_deducted or StockMovement.objects.filter(related_order_item=item).exists():
        raise ValidationError('تم تسجيل خصم مخزون لهذا العنصر. لا يمكن تقسيمه.')
    if quantity > item.quantity:
        raise ValidationError('الكمية المطلوبة أكبر من الكمية المتاحة.')
    if quantity == item.quantity and order.items.count() == 1:
        raise ValidationError('لنقل الطلب بالكامل استخدم «نقل الطلب إلى حساب آخر».')
    if item.line_total_syp_snapshot is not None and (
        item.line_total_syp_snapshot != item.quantity * item.unit_price_syp_snapshot
    ):
        raise ValidationError('سعر العنصر التاريخي غير قابل للتقسيم تلقائياً.')
    if item.estimated_line_cost_syp_snapshot is not None and (
        item.estimated_unit_cost_syp_snapshot is None
        or item.estimated_line_cost_syp_snapshot != item.quantity * item.estimated_unit_cost_syp_snapshot
    ):
        raise ValidationError('تكلفة العنصر التاريخية تحتاج مراجعة قبل التقسيم.')
    if item.estimated_line_margin_syp_snapshot is not None and (
        item.estimated_line_margin_syp_snapshot
        != item.quantity * item.unit_price_syp_snapshot - (item.estimated_line_cost_syp_snapshot or 0)
    ):
        raise ValidationError('هامش العنصر التاريخي يحتاج مراجعة قبل التقسيم.')

    original_quantity = item.quantity
    unit_margin_snapshot = (
        item.estimated_line_margin_syp_snapshot // original_quantity
        if item.estimated_line_margin_syp_snapshot is not None else None
    )
    destination_order = Order.objects.create(
        visit=destination,
        table=destination.table,
        member=order.member,
        service_mode=order.service_mode,
        fulfillment_mode=order.fulfillment_mode,
        status=Order.Status.NEW,
        notes=f'قسم من الطلب {order.display_number}',
        entered_by=actor, entry_source=Order.EntrySource.SYSTEM,
    )
    if quantity == original_quantity:
        # Preserve the original OrderItem ID and complete snapshots. No new
        # stock-deduction trigger or duplicated line.
        OrderItem.objects.filter(pk=item.pk).update(
            order=destination_order, updated_at=timezone.now()
        )
        moved_item = item
    else:
        remaining_quantity = original_quantity - quantity
        item.quantity = remaining_quantity
        item.line_total_syp_snapshot = remaining_quantity * item.unit_price_syp_snapshot
        if item.estimated_line_cost_syp_snapshot is not None:
            item.estimated_line_cost_syp_snapshot = (
                remaining_quantity * item.estimated_unit_cost_syp_snapshot
            )
        if item.estimated_line_margin_syp_snapshot is not None:
            item.estimated_line_margin_syp_snapshot = remaining_quantity * unit_margin_snapshot
        OrderItem.objects.filter(pk=item.pk).update(
            quantity=item.quantity,
            line_total_syp_snapshot=item.line_total_syp_snapshot,
            estimated_line_cost_syp_snapshot=item.estimated_line_cost_syp_snapshot,
            estimated_line_margin_syp_snapshot=item.estimated_line_margin_syp_snapshot,
            updated_at=timezone.now(),
        )
        # bulk_create intentionally preserves original nullable snapshot fields
        # without recalculating costs from today's product configuration.
        moved_item = OrderItem.objects.bulk_create([OrderItem(
            order=destination_order,
            product=item.product,
            quantity=quantity,
            product_name_ar_snapshot=item.product_name_ar_snapshot,
            product_name_en_snapshot=item.product_name_en_snapshot,
            unit_price_syp_snapshot=item.unit_price_syp_snapshot,
            selected_options_snapshot=item.selected_options_snapshot,
            item_note=item.item_note,
            line_total_syp_snapshot=quantity * item.unit_price_syp_snapshot,
            estimated_unit_cost_syp_snapshot=item.estimated_unit_cost_syp_snapshot,
            estimated_line_cost_syp_snapshot=(
                quantity * item.estimated_unit_cost_syp_snapshot
                if item.estimated_unit_cost_syp_snapshot is not None else None
            ),
            estimated_line_margin_syp_snapshot=(
                quantity * unit_margin_snapshot
                if unit_margin_snapshot is not None else None
            ),
            prep_station=item.prep_station,
            prep_status=item.prep_status,
            stock_deducted=False,
        )])[0]

    now = timezone.now()
    for visit in visits.values():
        visit.last_activity_at = now
        visit.save(update_fields=['last_activity_at', 'updated_at'])
    ActivityLog.objects.create(
        actor=actor,
        action='visit.order_item_split',
        details={
            'source_visit_id': source_visit_id,
            'destination_visit_id': destination_visit_id,
            'source_order_id': order.pk,
            'new_order_id': destination_order.pk,
            'source_item_id': item_id,
            'moved_item_id': moved_item.pk,
            'original_quantity': original_quantity,
            'moved_quantity': quantity,
            'unit_price_syp_snapshot': item.unit_price_syp_snapshot,
            'reason': reason,
            'idempotency_key': request_token,
        },
    )
    return destination_order
