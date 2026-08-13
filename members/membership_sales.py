"""Authoritative commercial checkout for memberships."""

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog, Category, Order, OrderItem, Payment, Product
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect as collect_order_payment
from members.models import MembershipPlan, MembershipSubscription


@dataclass(frozen=True)
class MembershipSale:
    subscription: MembershipSubscription
    created: bool


def _add_months(value, months):
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def calculate_membership_end(plan, starts_at, explicit_ends_at=None):
    if explicit_ends_at is not None:
        return explicit_ends_at
    value, unit = plan.term_value, plan.term_unit
    if value and unit:
        if unit == MembershipPlan.TermUnit.DAY:
            return starts_at + timedelta(days=value)
        if unit == MembershipPlan.TermUnit.WEEK:
            return starts_at + timedelta(weeks=value)
        if unit == MembershipPlan.TermUnit.MONTH:
            return _add_months(starts_at, value)
        if unit == MembershipPlan.TermUnit.YEAR:
            return _add_months(starts_at, value * 12)
    if plan.billing_period == MembershipPlan.BillingPeriod.MONTHLY:
        return _add_months(starts_at, 1)
    if plan.billing_period == MembershipPlan.BillingPeriod.ANNUAL:
        return _add_months(starts_at, 12)
    if plan.billing_period == MembershipPlan.BillingPeriod.FIXED_TERM:
        raise ValidationError({'ends_at': 'تحتاج الخطة محددة المدة إلى مدة واضحة أو تاريخ انتهاء.'})
    return None


def _fingerprint(*, member_id, plan_id, gross, starts_at, ends_at, payment_method):
    payload = {
        'member': member_id, 'plan': plan_id, 'gross': gross,
        'starts_at': starts_at.isoformat(),
        'ends_at': ends_at.isoformat() if ends_at else None,
        'payment_method': payment_method,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _membership_product(plan):
    if plan.catalog_product_id:
        product = plan.catalog_product
        if product.product_type != Product.ProductType.MEMBERSHIP or product.item_type != Product.ItemType.MEMBERSHIP:
            raise ValidationError({'plan': 'منتج الكتالوج المرتبط ليس منتج عضوية صالحاً.'})
        return product
    category, _ = Category.objects.get_or_create(
        name_ar='خدمات العضوية', defaults={'name_en': 'Membership services'})
    product = Product.objects.create(
        category=category, name_ar=plan.name_ar, name_en=plan.name_en,
        price_syp=plan.price_syp, product_type=Product.ProductType.MEMBERSHIP,
        item_type=Product.ItemType.MEMBERSHIP, is_available=True,
        visible_on_pos=False, orderable_on_pos=False, visible_on_qr=False,
        orderable_on_qr=False, available_for_events=False,
        available_for_takeaway=False, requires_preparation=False,
        not_discountable=True,
    )
    plan.catalog_product = product
    plan.save(update_fields=['catalog_product', 'updated_at'])
    return product


def _activate_committed_subscription(subscription_id, actor_id):
    subscription = MembershipSubscription.objects.get(pk=subscription_id)
    try:
        subscription.activate()
        ActivityLog.objects.create(
            actor_id=actor_id, action='membership.sale_activated',
            details={'subscription_id': str(subscription.uuid)})
    except Exception as exc:
        # activate() currently saves before provisioning. Restore a recoverable state
        # without touching the already committed order or payment.
        MembershipSubscription.objects.filter(pk=subscription_id).update(
            status=MembershipSubscription.Status.PENDING, activated_at=None,
            activation_error=str(exc)[:1000])
        ActivityLog.objects.create(
            actor_id=actor_id, action='membership.internet_activation_failed',
            details={'subscription_id': str(subscription.uuid), 'error': str(exc)[:500]})
        # Retain the established aggregate event name for existing audit consumers.
        ActivityLog.objects.create(
            actor_id=actor_id, action='membership.sale_activation_failed',
            details={'subscription_id': str(subscription.uuid), 'error': str(exc)[:500]})


def create_membership_sale(*, member, plan, payment_method, actor, idempotency_key,
                           starts_at=None, ends_at=None, payment_amount_syp=None):
    """Atomically commit one full membership sale, then activate it after commit."""
    if not actor or not user_has_capability(actor, 'members/internet'):
        raise PermissionDenied('لا تملك صلاحية بيع العضويات.')
    key = (idempotency_key or '').strip()
    if not key or len(key) > 120:
        raise ValidationError({'idempotency_key': 'رمز إعادة المحاولة مطلوب وصالح.'})
    starts_at = starts_at or timezone.now()
    if timezone.is_naive(starts_at):
        raise ValidationError({'starts_at': 'تاريخ البداية يجب أن يتضمن المنطقة الزمنية.'})

    with transaction.atomic():
        # catalog_product is nullable: keep it out of PostgreSQL's locked query.
        plan = MembershipPlan.objects.select_for_update().get(pk=plan.pk)
        member = type(member).objects.select_for_update().get(pk=member.pk)
        existing = MembershipSubscription.objects.select_related('order', 'payment').filter(
            sale_idempotency_key=key).first()
        fingerprint_gross = existing.gross_amount_syp if existing else plan.price_syp
        resolved_end = calculate_membership_end(plan, starts_at, ends_at)
        if resolved_end is not None and resolved_end <= starts_at:
            raise ValidationError({'ends_at': 'تاريخ الانتهاء يجب أن يكون بعد تاريخ البداية.'})
        fingerprint = _fingerprint(
            member_id=member.pk, plan_id=plan.pk, gross=fingerprint_gross,
            starts_at=starts_at, ends_at=resolved_end, payment_method=payment_method)
        if existing:
            if existing.sale_request_fingerprint != fingerprint:
                raise ValidationError({'idempotency_key': 'استُخدم رمز المحاولة لطلب عضوية مختلف.'})
            if (existing.status == MembershipSubscription.Status.PENDING
                    and existing.starts_at <= timezone.now()):
                transaction.on_commit(
                    lambda: _activate_committed_subscription(existing.pk, actor.pk))
            return MembershipSale(existing, False)
        if not plan.is_active or not plan.visible_to_staff:
            raise ValidationError({'plan': 'الخطة غير متاحة للبيع للموظفين.'})
        gross = int(plan.price_syp)
        if gross < 0:
            raise ValidationError({'plan': 'سعر الخطة لا يمكن أن يكون سالباً.'})
        if gross > 0:
            if payment_method not in {Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER}:
                raise ValidationError({'payment_method': 'العضوية المدفوعة تتطلب دفعة كاملة محصلة.'})
            if payment_amount_syp is not None and int(payment_amount_syp) != gross:
                raise ValidationError({'payment_amount_syp': 'يجب تحصيل كامل سعر العضوية.'})
        elif payment_method not in {'', None, Payment.Method.FREE}:
            raise ValidationError({'payment_method': 'الخطة المجانية لا تسجل دفعة محصلة.'})

        product = _membership_product(plan)
        order = Order.objects.create(member=member, notes=f'Membership sale: {plan.code}')
        OrderItem.objects.create(
            order=order, product=product, quantity=1,
            product_name_ar_snapshot=plan.name_ar,
            product_name_en_snapshot=plan.name_en,
            unit_price_syp_snapshot=gross, line_total_syp_snapshot=gross,
            prep_status=OrderItem.PrepStatus.NO_PREP)
        payment = None
        if gross:
            payment = collect_order_payment(order, PostingContext(
                actor=actor, business_date=timezone.localdate(),
                idempotency_key=f'membership-sale:{key}:payment',
                channel='membership-sale'), gross, payment_method)
        subscription = MembershipSubscription.objects.create(
            member=member, plan=plan, starts_at=starts_at, ends_at=resolved_end,
            status=MembershipSubscription.Status.PENDING, order=order, payment=payment,
            gross_amount_syp=gross, created_by=actor,
            sale_idempotency_key=key, sale_request_fingerprint=fingerprint,
            is_complimentary=(gross == 0),
            notes='عضوية مجانية' if gross == 0 else '')
        ActivityLog.objects.create(actor=actor, action='membership.sale_created', details={
            'subscription_id': str(subscription.uuid), 'member_id': member.pk,
            'plan_id': plan.pk, 'order_id': order.pk,
            'payment_id': payment.pk if payment else None, 'gross_amount_syp': gross,
            'complimentary': gross == 0,
        })
        if starts_at <= timezone.now():
            transaction.on_commit(lambda: _activate_committed_subscription(subscription.pk, actor.pk))
    return MembershipSale(subscription, True)
