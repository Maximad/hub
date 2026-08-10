"""Commercial Internet policy engine; deliberately independent of routers and views."""
import calendar
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import (ActivityLog, InternetAccessDevice, InternetEntitlement,
                         InternetPackage, InternetRevenueShare, InternetSession)


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
                       purchased_at=None):
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
        partner=package.partner, gross_amount_syp=package.price_syp,
        status=InternetEntitlement.Status.ACTIVE if activate else InternetEntitlement.Status.PENDING,
    )
    ActivityLog.objects.create(actor=created_by, action='internet.entitlement_created', details={'entitlement': str(entitlement.public_code), 'voucher': entitlement.access_code})
    snapshot_revenue_share(entitlement)
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
    session.save(update_fields=['ended_at', 'end_time', 'actual_duration_minutes', 'duration_minutes', 'status', 'ended_by', 'updated_at'])
    if session.entitlement_id and session.entitlement.access_mode == InternetPackage.AccessMode.ALLOWANCE:
        ent = InternetEntitlement.objects.select_for_update().get(pk=session.entitlement_id)
        consume = min(minutes, ent.minutes_remaining or 0)
        InternetEntitlement.objects.filter(pk=ent.pk).update(minutes_used=F('minutes_used') + consume)
    ActivityLog.objects.create(actor=actor, action='internet.session_ended', details={'session': session.pk, 'minutes': minutes})
    return session


def snapshot_revenue_share(entitlement, business_date=None):
    if not entitlement.partner_id:
        return None
    percent = entitlement.package.partner_share_percent
    if percent is None: percent = entitlement.partner.revenue_share_percent
    gross = Decimal(entitlement.gross_amount_syp)
    partner_amount = (gross * Decimal(percent) / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return InternetRevenueShare.objects.get_or_create(entitlement=entitlement, defaults={
        'partner': entitlement.partner, 'package': entitlement.package, 'order': entitlement.order,
        'payment': entitlement.payment, 'gross_amount_syp': gross, 'share_percent': percent,
        'partner_amount_syp': partner_amount, 'hub_amount_syp': gross - partner_amount,
        'business_date': business_date or timezone.localdate(),
    })[0]

