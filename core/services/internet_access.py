"""Commercial Internet policy engine; deliberately independent of routers and views."""
import calendar
import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from core.models import (ActivityLog, Category, InternetAccessDevice, InternetEntitlement,
                         InternetPackage, InternetPartner, InternetRevenueShare, InternetRevenueShareAdjustment,
                         InternetSession, InternetUsageLedger, Order, OrderItem, Payment, Product)


def _business_date(at):
    """Customer allowance days always follow Django's configured local timezone."""
    return timezone.localtime(at).date()


def _next_business_midnight(at):
    local = timezone.localtime(at)
    return timezone.make_aware(
        timezone.datetime.combine(local.date() + timedelta(days=1), timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )


def daily_minutes_used(entitlement, business_date=None):
    business_date = business_date or timezone.localdate()
    return (entitlement.usage_ledger.filter(business_date=business_date)
            .aggregate(total=Sum('minutes'))['total'] or 0)


def daily_minutes_remaining(entitlement, at=None, *, include_reservations=True):
    if entitlement.daily_minutes_limit is None:
        return None
    at = at or timezone.now()
    business_date = _business_date(at)
    used = daily_minutes_used(entitlement, business_date)
    reserved = 0
    if include_reservations:
        reserved = (entitlement.sessions.filter(
            status=InternetSession.Status.ACTIVE,
            reservation_business_date=business_date,
            reserved_minutes__isnull=False,
        ).aggregate(total=Sum('reserved_minutes'))['total'] or 0)
    return max(entitlement.daily_minutes_limit - used - reserved, 0)


def get_effective_network_allowance(entitlement, at=None, *, include_session_limit=True,
                                    include_reservations=True):
    """Return the single authoritative finite allowance intersection, or ``None``.

    Validity is floored to complete minutes so authorization can never extend beyond
    expiry. A daily-limited authorization is also bounded by local midnight.
    """
    at = at or timezone.now()
    limits = []
    if entitlement.total_minutes_allowed is not None:
        reserved = 0
        if include_reservations:
            reserved = (entitlement.sessions.filter(status=InternetSession.Status.ACTIVE,
                reserved_minutes__isnull=False).aggregate(total=Sum('reserved_minutes'))['total'] or 0)
        limits.append(max(entitlement.total_minutes_allowed - entitlement.minutes_used - reserved, 0))
    elif entitlement.access_mode == InternetPackage.AccessMode.MEMBERSHIP_CREDIT:
        balance = max(getattr(entitlement.subscription, 'remaining_internet_minutes', 0) or 0, 0)
        reserved = 0
        if include_reservations:
            reserved = (entitlement.sessions.filter(status=InternetSession.Status.ACTIVE,
                reserved_minutes__isnull=False).aggregate(total=Sum('reserved_minutes'))['total'] or 0)
        limits.append(max(balance - reserved, 0))
    daily = daily_minutes_remaining(entitlement, at, include_reservations=include_reservations)
    if daily is not None:
        limits.append(daily)
    if include_session_limit and entitlement.session_minutes_limit is not None:
        limits.append(entitlement.session_minutes_limit)
    deadlines = [deadline for deadline in (entitlement.valid_until,
        _next_business_midnight(at) if entitlement.daily_minutes_limit is not None else None) if deadline]
    if deadlines:
        seconds = max((min(deadlines) - at).total_seconds(), 0)
        limits.append(int(seconds // 60))
    return min(limits) if limits else None


def get_default_internet_partner():
    """Return the active default provider, without ever inferring one from existing data."""
    return InternetPartner.objects.filter(active=True, is_default=True).first()


def resolve_internet_partner(package):
    """Resolve a new sale's provider, failing closed on an explicit bad override."""
    if package.partner_id:
        if not package.partner.active:
            raise ValidationError('لا يمكن تعيين شريك غير فعّال لهذه الباقة.')
        return package.partner
    return get_default_internet_partner()


def normalize_mac_address(value):
    """Validate and return the canonical AA:BB:CC:DD:EE:FF device identity."""
    compact = re.sub(r'[:-]', '', (value or '').strip())
    if not re.fullmatch(r'[0-9A-Fa-f]{12}', compact):
        raise ValidationError({'device_mac': 'عنوان MAC غير صالح. الصيغة المطلوبة AA:BB:CC:DD:EE:FF.'})
    compact = compact.upper()
    return ':'.join(compact[index:index + 2] for index in range(0, 12, 2))


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
                       purchased_at=None, charged_amount_syp=None, pricing_benefit=None, visit=None):
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    if idempotency_key:
        existing = InternetEntitlement.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
    if not package.is_active:
        raise ValidationError('لا يمكن بيع باقة غير فعالة.')
    if package.member_only:
        from members.benefits import is_member_eligible_for_internet_package
        if not is_member_eligible_for_internet_package(member, package, purchased_at):
            raise ValidationError('هذه الباقة تتطلب اشتراك عضوية مؤهلاً وفعّالاً حالياً.')
    if member is None and not package.guest_allowed:
        raise ValidationError('هذه الباقة لا تسمح للزوار.')
    if package.access_mode == package.AccessMode.MEMBERSHIP_CREDIT and (member is None or subscription is None):
        raise ValidationError('باقة رصيد العضوية تتطلب عضواً واشتراكاً فعالاً.')
    if package.access_mode == package.AccessMode.MEMBERSHIP_CREDIT:
        from members.models import MembershipSubscription
        subscription = MembershipSubscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.member_id != member.pk:
            raise ValidationError('اشتراك رصيد الإنترنت لا يعود لهذا العضو.')
    now = purchased_at or timezone.now()
    activate = package.activation_policy == package.ActivationPolicy.ON_PURCHASE
    partner = resolve_internet_partner(package)
    partner_share_percent = resolve_partner_share_percent(package, partner)
    if partner is None and partner_share_percent is not None:
        raise ValidationError('لا يمكن تحديد نسبة حصة دون شريك إنترنت فعّال.')
    if package.bandwidth_profile_id and not package.bandwidth_profile.is_active:
        raise ValidationError('ملف سرعة الباقة غير فعّال.')
    entitlement = InternetEntitlement.objects.create(
        package=package, member=member, visit=visit, guest_name=guest_name, guest_phone=guest_phone,
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
    ActivityLog.objects.create(actor=created_by, action='internet.entitlement_created',
                               details={'entitlement': str(entitlement.public_code)})
    snapshot_revenue_share(entitlement, share_percent=partner_share_percent)
    return entitlement


@transaction.atomic
def activate_entitlement(entitlement, *, actor=None, at=None):
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.effective_status(at) in {
            entitlement.Status.EXPIRED, entitlement.Status.CANCELLED,
            entitlement.Status.SUSPENDED}:
        raise ValidationError('لا يمكن تفعيل استحقاق منتهٍ أو ملغى أو معلّق.')
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
    return _register_device_locked(entitlement, device_mac, nickname)


def _register_device_locked(entitlement, device_mac, nickname=''):
    """Register while the caller holds the entitlement lock."""
    if entitlement.effective_status() != entitlement.Status.ACTIVE:
        raise ValidationError('الاستحقاق غير فعال.')
    device_mac = normalize_mac_address(device_mac)
    device = InternetAccessDevice.objects.filter(entitlement=entitlement, device_mac=device_mac).first()
    if device:
        device.is_active = True; device.nickname = nickname or device.nickname; device.save()
        return device
    if entitlement.devices.count() >= entitlement.max_registered_devices:
        raise ValidationError('تم بلوغ الحد الأقصى للأجهزة المسجلة.')
    return InternetAccessDevice.objects.create(entitlement=entitlement, device_mac=device_mac, nickname=nickname)


@transaction.atomic
def start_usage_session(entitlement, *, actor=None, device_mac='', ip_address='', at=None, visit=None):
    # Membership lifecycle transitions lock subscription -> entitlement.  Follow
    # that order here as well, while keeping the entitlement row as the
    # authoritative session-start lock.  In particular, do not select_related()
    # nullable provenance rows into the FOR UPDATE query: PostgreSQL cannot lock
    # the nullable side of those outer joins.
    subscription = None
    subscription_id = (InternetEntitlement.objects.filter(pk=entitlement.pk)
                       .values_list('subscription_id', flat=True).get())
    if subscription_id:
        from members.models import MembershipSubscription
        subscription = MembershipSubscription.objects.select_for_update().get(pk=subscription_id)
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.subscription_id != subscription_id:
        raise ValidationError('تغيّر اشتراك مصدر الاستحقاق؛ يرجى إعادة المحاولة.')
    at = at or timezone.now()
    # Membership provenance is authoritative even if reconciliation has not yet
    # copied a frozen/terminal state onto the entitlement.
    if subscription is not None and not subscription.is_active_at(at):
        raise ValidationError('اشتراك العضوية المصدر غير فعال.')
    # Business first use is precisely creation of the first real Hub usage session.
    if entitlement.activation_policy == InternetPackage.ActivationPolicy.ON_FIRST_USE and not entitlement.activated_at:
        entitlement = activate_entitlement(entitlement, actor=actor, at=at)
    if entitlement.effective_status(at) != entitlement.Status.ACTIVE:
        raise ValidationError('الاستحقاق غير فعال.')
    # A timed purchase is one finite session, not a reusable duration bucket.  The
    # entitlement row lock makes this check authoritative under concurrent starts.
    if (entitlement.access_mode == InternetPackage.AccessMode.TIMED_SESSION and
            InternetSession.objects.filter(entitlement=entitlement).exists()):
        raise ValidationError('تم استخدام هذه الباقة ذات الجلسة الواحدة.')
    if entitlement.access_mode == InternetPackage.AccessMode.MEMBERSHIP_CREDIT:
        if not entitlement.subscription_id:
            raise ValidationError('لا يوجد اشتراك مرتبط برصيد العضوية.')
        if subscription.member_id != entitlement.member_id:
            raise ValidationError('الاشتراك لا يعود لصاحب الاستحقاق.')
        if not subscription.is_active_at(at):
            raise ValidationError('اشتراك العضوية غير فعال أو منتهي.')
        if not subscription.remaining_internet_minutes or subscription.remaining_internet_minutes < 0:
            raise ValidationError('نفد رصيد دقائق العضوية.')
    active = InternetSession.objects.filter(entitlement=entitlement, status=InternetSession.Status.ACTIVE).count()
    if active >= entitlement.max_concurrent_devices:
        raise ValidationError('تم بلوغ حد الأجهزة المتزامنة.')
    if device_mac: _register_device_locked(entitlement, device_mac)
    authorized = get_effective_network_allowance(entitlement, at)
    if authorized is not None and authorized <= 0:
        raise ValidationError('لا توجد دقائق قابلة للاستخدام حالياً.')
    deadlines = []
    if authorized is not None:
        deadlines.append(at + timedelta(minutes=authorized))
    if entitlement.valid_until:
        deadlines.append(entitlement.valid_until)
    if entitlement.daily_minutes_limit is not None:
        deadlines.append(_next_business_midnight(at))
    authorized_until = min(deadlines) if deadlines else None
    return InternetSession.objects.create(entitlement=entitlement, package=entitlement.package, visit=visit,
        member=entitlement.member, guest_name=entitlement.guest_name, guest_phone=entitlement.guest_phone,
        customer_name=entitlement.guest_name, customer_phone=entitlement.guest_phone,
        start_time=at, started_at=at, billing_mode=InternetSession.BillingMode.PREPAID,
        status=InternetSession.Status.ACTIVE, started_by=actor, device_mac=device_mac,
        ip_address=ip_address, access_code=entitlement.access_code,
        bandwidth_profile=entitlement.bandwidth_profile_code, network_provider=entitlement.network_backend,
        reserved_minutes=authorized, authorized_minutes=authorized,
        authorized_until=authorized_until, reservation_business_date=_business_date(at))


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
    membership_consume = 0
    if session.entitlement_id:
        ent = InternetEntitlement.objects.select_for_update().get(pk=session.entitlement_id)
        if ent.access_mode == InternetPackage.AccessMode.ALLOWANCE:
            # New sessions settle against their immutable reservation. Legacy nullable
            # sessions conservatively use the balance available while holding this lock.
            cap = session.reserved_minutes
            if cap is None:
                cap = max((ent.total_minutes_allowed or 0) - ent.minutes_used, 0)
            consume = min(minutes, cap)
            ent.minutes_used = min(ent.minutes_used + consume, ent.total_minutes_allowed or 0)
            ent.save(update_fields=['minutes_used', 'updated_at'])
        elif ent.access_mode == InternetPackage.AccessMode.MEMBERSHIP_CREDIT:
            from members.models import MembershipSubscription
            if not ent.subscription_id:
                raise ValidationError('لا يوجد اشتراك مرتبط برصيد العضوية.')
            subscription = MembershipSubscription.objects.select_for_update().get(
                pk=ent.subscription_id)
            if subscription.member_id != ent.member_id:
                raise ValidationError('الاشتراك لا يعود لصاحب الاستحقاق.')
            # The same cap-at-balance rule applies when concurrent sessions finish.
            balance = max(subscription.remaining_internet_minutes or 0, 0)
            cap = session.reserved_minutes if session.reserved_minutes is not None else balance
            membership_consume = consume = min(minutes, cap, balance)
            if consume:
                updated = MembershipSubscription.objects.filter(
                    pk=subscription.pk,
                    remaining_internet_minutes__gte=consume,
                ).update(remaining_internet_minutes=F('remaining_internet_minutes') - consume)
                if updated != 1:
                    raise ValidationError('تغيّر رصيد العضوية؛ يرجى إعادة المحاولة.')
    session.allowance_minutes_consumed = consume
    session.overrun_minutes = (max(minutes - session.authorized_minutes, 0)
                               if session.authorized_minutes is not None else 0)
    session.member_minutes_used = membership_consume
    session.save(update_fields=['ended_at', 'end_time', 'actual_duration_minutes', 'duration_minutes',
                                'allowance_minutes_consumed', 'member_minutes_used', 'overrun_minutes', 'status',
                                'ended_by', 'updated_at'])
    if session.entitlement_id and consume:
        allocations = {}
        # Each rounded usage minute belongs to the local date on which that minute began.
        for offset in range(consume):
            day = _business_date(session.effective_started_at + timedelta(minutes=offset))
            allocations[day] = allocations.get(day, 0) + 1
        InternetUsageLedger.objects.bulk_create([
            InternetUsageLedger(entitlement_id=session.entitlement_id, session=session,
                                business_date=day, minutes=value)
            for day, value in allocations.items()
        ], ignore_conflicts=True)
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
        'partner': entitlement.partner, 'partner_name_snapshot': entitlement.partner_name_snapshot,
        'package': entitlement.package, 'order': entitlement.order,
        'payment': entitlement.payment, 'gross_amount_syp': gross, 'share_percent': percent,
        'partner_amount_syp': partner_amount, 'hub_amount_syp': gross - partner_amount,
        'business_date': business_date or timezone.localdate(),
    })[0]


def create_commercial_sale(package, *, payment_method, member=None, guest_name='', guest_phone='',
                           subscription=None, actor=None, idempotency_key, visit=None):
    """Commit checkout once, then provision as an independently retryable side effect."""
    sale_at = timezone.now()
    with transaction.atomic():
        package = InternetPackage.objects.select_for_update().get(pk=package.pk)
        from members.benefits import resolve_internet_price
        charged_price, pricing_benefit = resolve_internet_price(member, package, sale_at)
        charged_price = int(charged_price)
        effective_payment_method = (payment_method if payment_method in Payment.Method.values
                                    else Payment.Method.UNPAID)
        identity = {'member_id': member.pk if member else None,
                    'guest_name': '' if member else guest_name.strip(),
                    'guest_phone': '' if member else guest_phone.strip()}
        fingerprint_data = {
            'package_id': package.pk, **identity, 'charged_amount_syp': charged_price,
            'payment_method': effective_payment_method,
            'subscription_id': subscription.pk if subscription else None,
        }
        # Preserve the exact pre-visit fingerprint for every historical/manual retry.
        if visit is not None:
            fingerprint_data['visit_id'] = visit.pk
        fingerprint = hashlib.sha256(json.dumps(
            fingerprint_data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        existing = InternetEntitlement.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if not existing.sale_request_fingerprint or existing.sale_request_fingerprint != fingerprint:
                raise ValidationError({'idempotency_key': 'استُخدم رمز المحاولة لعملية بيع إنترنت مختلفة.'})
            entitlement = existing
        else:
            complimentary = package.access_mode == package.AccessMode.MEMBERSHIP_CREDIT or charged_price == 0
            order = payment = None
            if not complimentary:
                order = Order.objects.create(member=member, visit=visit, notes=f'Internet package: {package.name_ar}')
                category, _ = Category.objects.get_or_create(name_ar='خدمات الإنترنت', defaults={'name_en': 'Internet services'})
                product, _ = Product.objects.get_or_create(
                    category=category, name_ar=package.name_ar, product_type=Product.ProductType.INTERNET,
                    defaults={'price_syp': package.price_syp, 'item_type': Product.ItemType.SERVICE,
                              'service_type': Product.ServiceType.INTERNET, 'visible_on_pos': False,
                              'orderable_on_pos': False, 'visible_on_qr': False, 'requires_preparation': False})
                OrderItem.objects.create(order=order, product=product, quantity=1,
                    product_name_ar_snapshot=package.name_ar, unit_price_syp_snapshot=charged_price,
                    line_total_syp_snapshot=charged_price)
                method = effective_payment_method
                if method != Payment.Method.UNPAID:
                    from core.services.posting.context import PostingContext
                    from core.services.posting.order_payments import collect
                    payment = collect(order, PostingContext(
                        actor=actor, business_date=timezone.localdate(sale_at),
                        idempotency_key=f'internet-sale:{idempotency_key}:payment',
                        channel='internet-sale',
                    ), charged_price, method)
            entitlement = create_entitlement(package, member=member, guest_name=guest_name,
                guest_phone=guest_phone, order=order, payment=payment, subscription=subscription,
                created_by=actor, idempotency_key=idempotency_key, purchased_at=sale_at,
                charged_amount_syp=charged_price, pricing_benefit=pricing_benefit, visit=visit)
            entitlement.sale_request_fingerprint = fingerprint
            entitlement.save(update_fields=['sale_request_fingerprint', 'updated_at'])

        # The durable row is committed with the sale; only its on-commit callback may
        # touch a network backend. Repeated checkout resolves to the same logical job.
        from core.models import InternetNetworkOperation
        from core.services.network_operations import enqueue_network_operation
        enqueue_network_operation(entitlement, InternetNetworkOperation.Operation.PROVISION)
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
