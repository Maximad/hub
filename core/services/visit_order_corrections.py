"""Audited, finance-safe reassignment of an entire unpaid order between visits.

This is deliberately not an item split, payment reversal or settlement transfer.
Account history, currency posting and financial permissions remain independent.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, HubVisit, Order, OrderItem, Payment


@transaction.atomic
def move_unpaid_order(*, actor, order_id, source_visit_id, destination_visit_id, reason):
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise ValidationError('اذكر سبب النقل بثلاثة أحرف على الأقل.')
    if source_visit_id == destination_visit_id:
        raise ValidationError('اختر حساباً مختلفاً لنقل الطلب.')
    if destination_visit_id is None and source_visit_id is None:
        raise ValidationError('يلزم تحديد حساب مصدر أو وجهة.')

    # Visit locks are acquired in PK order to avoid opposite-direction deadlocks.
    ids = sorted({pk for pk in (source_visit_id, destination_visit_id) if pk is not None})
    visits = {
        visit.pk: visit
        for visit in HubVisit.objects.select_for_update()
        .filter(pk__in=ids).order_by('pk')
    }
    if len(visits) != len(ids):
        raise ValidationError('أحد الحسابات غير موجود.')
    if any(visit.status != HubVisit.Status.OPEN for visit in visits.values()):
        raise ValidationError('لا يمكن نقل الطلب إلى حساب مغلق أو منه.')

    # Lock the order row alone, not any nullable FK outer joins.
    order = Order.objects.select_for_update(of=('self',)).filter(pk=order_id).first()
    if order is None or order.visit_id != source_visit_id:
        raise ValidationError('الطلب تغيّر منذ فتح الصفحة. حدّث الحساب وحاول من جديد.')
    if order.status == Order.Status.CANCELLED:
        raise ValidationError('لا يمكن نقل طلب ملغى.')
    if order.fulfillment_mode in {Order.FulfillmentMode.DELIVERY, Order.FulfillmentMode.TAKEAWAY}:
        raise ValidationError('طلبات التوصيل والتيك أواي تحتاج مراجعة متخصصة.')
    if order.status in {Order.Status.PREPARING, Order.Status.READY} or OrderItem.objects.filter(
        order=order, prep_status__in=[
            OrderItem.PrepStatus.ACCEPTED,
            OrderItem.PrepStatus.PREPARING,
            OrderItem.PrepStatus.READY,
        ]
    ).exists():
        raise ValidationError('الطلب قيد التحضير أو التسليم. أنهِ المرحلة قبل نقل الحساب.')

    # Block ALL historical payments, even reversed/inactive ones. Moving the
    # underlying revenue allocation needs a dedicated posting/reversal workflow.
    if Payment.objects.filter(order=order).exists():
        raise ValidationError('للطلب سجل دفعات. لا يمكن نقله دون تصحيح مالي معتمد.')
    if order.internet_entitlements.exists() or order.internet_sessions.exists():
        raise ValidationError('الطلب مرتبط بخدمات إنترنت. يجب تسويته من مسار متخصص.')
    if order.discounts.exists():
        raise ValidationError('الطلب مرتبط بخصومات. يحتاج نقلُه إلى مراجعة مالية.')

    destination = visits.get(destination_visit_id)
    if destination and order.member_id and order.member_id != destination.member_id:
        raise ValidationError('الطلب مرتبط بعضو مختلف عن صاحب الحساب الوجهة.')

    old_table_id = order.table_id
    # A visit transfer means the whole order, including table snapshot context.
    order.visit_id = destination_visit_id
    order.table_id = destination.table_id if destination else None
    order.save(update_fields=['visit', 'table', 'updated_at'])

    now = timezone.now()
    for visit in visits.values():
        visit.last_activity_at = now
        visit.save(update_fields=['last_activity_at', 'updated_at'])
    ActivityLog.objects.create(
        actor=actor,
        action='visit.order_transferred',
        details={
            'order_id': order.pk,
            'order_public_code': str(order.public_code),
            'source_visit_id': source_visit_id,
            'destination_visit_id': destination_visit_id,
            'old_table_id': old_table_id,
            'new_table_id': order.table_id,
            'reason': reason,
        },
    )
    return order
