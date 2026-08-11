"""Commercial Internet policy engine; deliberately independent of routers and views."""
import calendar
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import (ActivityLog, Category, InternetAccessDevice, InternetEntitlement,
                         InternetPackage, InternetPartner, InternetRevenueShare, InternetRevenueShareAdjustment,
                         InternetSession, Order, OrderItem, Payment, Product)


def get_default_internet_partner():
    """Return the active default provider, without ever inferring one from existing data."""
    return InternetPartner.objects.filter(active=True, is_default=True).first()


def resolve_internet_partner(package):
    """Resolve a sale's provider; an inactive package override is never selected."""
    if package.partner_id and package.partner.active:
        return package.partner
    return get_default_internet_partner()


def resolve_partner_share_percent(package, partner=None):
    """Resolve the commercial percentage to snapshot for a new sale."""
    if package.partner_share_percent is not None:
        return package.partner_share_percent
    partner = partner if partner is not None else resolve_internet_partner(package)
    return partner.revenue_share_percent if partner is not None else None


def effectively_active_entitlements(queryset=None, *, at=None):
    """Single database definition of runtime-active access; cleanup/cron is not required."""
    from django.db.models import Q
    queryset = queryset if queryset is not None else InternetEntitlement.objects.all()
    at = at or timezone.now()
    return queryset.filter(status=InternetEntitlement.Status.ACTIVE).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=at))


def validity_end(start, value, unit):
    """Return a deterministic, timezone-aware validity boundary."""
    if not value or not unit:
        return None
    if unit == InternetPackage.ValidityUnit.MINUTES:
        return start + timedelta(minutes=value)
    if unit == InternetPackage.ValidityUnit.DAYS:
        return start + timedelta(days=value)
    if unit == InternetPackage.ValidityUnit.WEEKS:
        return start + timedelta(weeks=value)
    if unit == InternetPackage.ValidityUnit.MONTHS:
        month_index = start.month - 1 + value
        year, month = start.year + month_index // 12, month_index % 12 + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return start.replace(year=year, month=month, day=day)
    raise ValidationError({'validity_unit': 'وحدة صلاحية غير مدعومة.'})


@transaction.atomic
def create_entitlement(package, *, member=None, guest_name='', guest_phone='', order=None,
                       payment=None, subscription=None, created_by=None, idempotency_key=None,
                       purchased_at=None, charged_amount_syp=None, pricing_benefit=None):
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    if idempotency_key:
        existing = InternetEntitlement.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
    if not package.is_active:
        raise ValidationError('لا يمكن بيع باقة غير فعالة.')
    if package.member_only and member is None:
        raise ValidationError('هذه الباقة للأعضاء فقط.')
    if member is None and not package.guest_allowed:
        raise ValidationError('هذه الباقة لا تسمح للزوار.')
    if package.access_mode == package.AccessMode.MEMBERSHIP_CREDIT and (member is None or subscription is None):
        raise ValidationError('باقة رصيد العضوية تتطلب عضواً واشتراكاً فعالاً.')
    now = purchased_at or timezone.now()
    activate = package.activation_policy == package.ActivationPolicy.ON_PURCHASE
    partner = resolve_internet_partner(package)
    partner_share_percent = resolve_partner_share_percent(package, partner)
    entitlement = InternetEntitlement.objects.create(
        package=package, member=member, guest_name=guest_name, guest_phone=guest_phone,
        order=order, payment=payment, subscription=subscription, created_by=created_by,
        idempotency_key=idempotency_key, access_mode=package.access_mode,
        activation_policy=package.activation_policy, activated_at=now if activate else None,
        valid_from=now if activate else None,
        valid_until=validity_end(now, package.validity_value, package.validity_unit) if activate else None,
        validity_value=package.validity_value, validity_unit=package.validity_unit,
        session_minutes_limit=package.session_minutes_limit or (package.duration_minutes or None),
        total_minutes_allowed=None if package.access_mode == package.AccessMode.MEMBERSHIP_CREDIT else package.total_minutes_limit,
        daily_minutes_limit=package.daily_minutes_limit,
        bandwidth_profile_code=package.bandwidth_profile.code if package.bandwidth_profile_id else '',
        max_concurrent_devices=package.max_concurrent_devices,
        max_registered_devices=package.max_registered_devices,
        partner=partner, partner_name_snapshot=partner.name if partner else '',
        partner_share_percent_snapshot=partner_share_percent,
        gross_amount_syp=package.price_syp if charged_amount_syp is None else charged_amount_syp,
        source_benefit_rule_id=(pricing_benefit.definition.get('rule_id') if pricing_benefit else None),
        status=InternetEntitlement.Status.ACTIVE if activate else InternetEntitlement.Status.PENDING,
    )
    ActivityLog.objects.create(actor=created_by, action='internet.entitlement_created', details={'entitlement': str(entitlement.public_code), 'voucher': entitlement.access_code})
    snapshot_revenue_share(entitlement, share_percent=partner_share_percent)
    return entitlement


@transaction.atomic
def activate_entitlement(entitlement, *, actor=None, at=None):
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.effective_status(at) in {entitlement.Status.EXPIRED, entitlement.Status.CANCELLED}:
        raise ValidationError('لا يمكن تفعيل استحقاق منتهٍ أو ملغى.')
    if entitlement.activated_at:
        return entitlement
    at = at or timezone.now()
    entitlement.activated_at = entitlement.valid_from = at
    entitlement.valid_until = validity_end(at, entitlement.validity_value, entitlement.validity_unit)
    entitlement.status = entitlement.Status.ACTIVE
    entitlement.save(update_fields=['activated_at', 'valid_from', 'valid_until', 'status', 'updated_at'])
    ActivityLog.objects.create(actor=actor, action='internet.entitlement_activated', details={'entitlement': str(entitlement.public_code)})
    return entitlement


@transaction.atomic
def register_device(entitlement, device_mac, nickname=''):
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.effective_status() != entitlement.Status.ACTIVE:
        raise ValidationError('الاستحقاق غير فعال.')
    device = InternetAccessDevice.objects.filter(entitlement=entitlement, device_mac__iexact=device_mac).first()
    if device:
        device.is_active = True; device.nickname = nickname or device.nickname; device.save()
        return device
    if entitlement.devices.count() >= entitlement.max_registered_devices:
        raise ValidationError('تم بلوغ الحد الأقصى للأجهزة المسجلة.')
    return InternetAccessDevice.objects.create(entitlement=entitlement, device_mac=device_mac.upper(), nickname=nickname)


@transaction.atomic
def start_usage_session(entitlement, *, actor=None, device_mac='', ip_address='', at=None):
    entitlement = InternetEntitlement.objects.select_for_update().select_related('member').get(pk=entitlement.pk)
    # Business first use is precisely creation of the first real Hub usage session.
    if entitlement.activation_policy == InternetPackage.ActivationPolicy.ON_FIRST_USE and not entitlement.activated_at:
        entitlement = activate_entitlement(entitlement, actor=actor, at=at)
    if entitlement.effective_status(at) != entitlement.Status.ACTIVE:
        raise ValidationError('الاستحقاق غير فعال.')
    if entitlement.minutes_remaining is not None and entitlement.minutes_remaining <= 0:
        raise ValidationError('نفد رصيد الدقائق.')
    active = InternetSession.objects.filter(entitlement=entitlement, status=InternetSession.Status.ACTIVE).count()
    if active >= entitlement.max_concurrent_devices:
        raise ValidationError('تم بلوغ حد الأجهزة المتزامنة.')
    if device_mac: register_device(entitlement, device_mac)
    at = at or timezone.now()
    return InternetSession.objects.create(entitlement=entitlement, package=entitlement.package,
        member=entitlement.member, guest_name=entitlement.guest_name, guest_phone=entitlement.guest_phone,
        customer_name=entitlement.guest_name, customer_phone=entitlement.guest_phone,
        start_time=at, started_at=at, billing_mode=InternetSession.BillingMode.PREPAID,
        status=InternetSession.Status.ACTIVE, started_by=actor, device_mac=device_mac,
        ip_address=ip_address, access_code=entitlement.access_code,
        bandwidth_profile=entitlement.bandwidth_profile_code, network_provider=entitlement.network_backend)


@transaction.atomic
def end_usage_session(session, *, actor=None, at=None):
    session = InternetSession.objects.select_for_update().select_related('entitlement').get(pk=session.pk)
    if session.status != session.Status.ACTIVE:
        return session
    at = at or timezone.now()
    seconds = max((at - session.effective_started_at).total_seconds(), 0)
    minutes = int((seconds + 59) // 60)
    session.ended_at = session.end_time = at
    session.actual_duration_minutes = session.duration_minutes = minutes
    session.status = session.Status.ENDED
    session.ended_by = actor
    consume = 0
    if session.entitlement_id and session.entitlement.access_mode == InternetPackage.AccessMode.ALLOWANCE:
        ent = InternetEntitlement.objects.select_for_update().get(pk=session.entitlement_id)
        consume = min(minutes, ent.minutes_remaining or 0)
        InternetEntitlement.objects.filter(pk=ent.pk).update(minutes_used=F('minutes_used') + consume)
    session.allowance_minutes_consumed = consume
    session.save(update_fields=['ended_at', 'end_time', 'actual_duration_minutes', 'duration_minutes',
                                'allowance_minutes_consumed', 'status', 'ended_by', 'updated_at'])
    ActivityLog.objects.create(actor=actor, action='internet.session_ended', details={'session': session.pk, 'minutes': minutes})
    return session


def snapshot_revenue_share(entitlement, business_date=None, share_percent=None):
    if not entitlement.partner_id:
        return None
    percent = share_percent
    if percent is None:
        percent = resolve_partner_share_percent(entitlement.package, entitlement.partner)
    if percent is None:
        return None
    gross = Decimal(entitlement.gross_amount_syp)
    partner_amount = (gross * Decimal(percent) / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return InternetRevenueShare.objects.get_or_create(entitlement=entitlement, defaults={
        'partner': entitlement.partner, 'package': entitlement.package, 'order': entitlement.order,
        'payment': entitlement.payment, 'gross_amount_syp': gross, 'share_percent': percent,
        'partner_amount_syp': partner_amount, 'hub_amount_syp': gross - partner_amount,
        'business_date': business_date or timezone.localdate(),
    })[0]


@transaction.atomic
def create_commercial_sale(package, *, payment_method, member=None, guest_name='', guest_phone='',
                           subscription=None, actor=None, idempotency_key):
    """Atomic staff checkout. The unique entitlement key is the server-side POST identity."""
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    existing = InternetEntitlement.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    from members.benefits import resolve_internet_price
    charged_price, pricing_benefit = resolve_internet_price(member, package)
    charged_price = int(charged_price)
    complimentary = package.access_mode == package.AccessMode.MEMBERSHIP_CREDIT or charged_price == 0
    order = payment = None
    if not complimentary:
        order = Order.objects.create(member=member, notes=f'Internet package: {package.name_ar}')
        category, _ = Category.objects.get_or_create(name_ar='خدمات الإنترنت', defaults={'name_en': 'Internet services'})
        product, _ = Product.objects.get_or_create(
            category=category, name_ar=package.name_ar, product_type=Product.ProductType.INTERNET,
            defaults={'price_syp': package.price_syp, 'item_type': Product.ItemType.SERVICE,
                      'service_type': Product.ServiceType.INTERNET, 'visible_on_pos': False,
                      'orderable_on_pos': False, 'visible_on_qr': False, 'requires_preparation': False})
        OrderItem.objects.create(order=order, product=product, quantity=1,
            product_name_ar_snapshot=package.name_ar, unit_price_syp_snapshot=charged_price,
            line_total_syp_snapshot=charged_price)
        method = payment_method if payment_method in Payment.Method.values else Payment.Method.UNPAID
        payment = Payment.objects.create(order=order, amount_syp=charged_price,
                                         method=method, created_by=actor)
    entitlement = create_entitlement(package, member=member, guest_name=guest_name,
        guest_phone=guest_phone, order=order, payment=payment, subscription=subscription,
        created_by=actor, idempotency_key=idempotency_key, charged_amount_syp=charged_price,
        pricing_benefit=pricing_benefit)
    from core.services.network_backends import get_network_backend
    get_network_backend(entitlement.network_backend).provision_access(entitlement)
    return entitlement


@transaction.atomic
def record_payment_reversal_adjustment(revenue_share, *, payment=None, kind='reversal', business_date=None):
    """Record one immutable negation of a realized sale; safe to retry."""
    payment = payment or revenue_share.payment
    key = f'internet-share:{revenue_share.pk}:{kind}:payment:{payment.pk if payment else "none"}'
    return InternetRevenueShareAdjustment.objects.get_or_create(idempotency_key=key, defaults={
        'revenue_share': revenue_share, 'payment': payment, 'kind': kind,
        'gross_delta_syp': -revenue_share.gross_amount_syp,
        'partner_delta_syp': -revenue_share.partner_amount_syp,
        'hub_delta_syp': -revenue_share.hub_amount_syp,
        'business_date': business_date or timezone.localdate(),
    })[0]
