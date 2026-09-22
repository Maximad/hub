"""Private owner/team Internet grants built on the existing entitlement engine.

These grants are operational access, not sales. New grants are assigned to Hub staff
user accounts, not public Member rows. Historical member-based grants remain valid
and revocable for backwards compatibility. Network work uses the normal entitlement
provisioning queue and never changes RouterOS configuration.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    ActivityLog,
    InternetBandwidthProfile,
    InternetEntitlement,
    InternetNetworkOperation,
    InternetPackage,
    Member,
)
from core.services.internet_access import validity_end
from core.services.internet_lifecycle import cancel_internet_entitlement
from core.services.network_operations import enqueue_network_operation


INTERNAL_OWNER_ORIGIN = 'internal_owner_grant'
INTERNAL_TEAM_ORIGIN = 'internal_team_grant'
INTERNAL_ORIGINS = (INTERNAL_OWNER_ORIGIN, INTERNAL_TEAM_ORIGIN)

GRANT_KIND_TO_ORIGIN = {
    'owner': INTERNAL_OWNER_ORIGIN,
    'team': INTERNAL_TEAM_ORIGIN,
}
GRANT_KIND_LABELS = {
    'owner': 'الإدارة',
    'team': 'الفريق',
}


def internal_grants():
    return InternetEntitlement.objects.filter(origin_type__in=INTERNAL_ORIGINS)


def _validate_grant_values(*, access_mode, validity_value, validity_unit,
                           total_minutes_allowed, max_concurrent_devices,
                           max_registered_devices):
    if access_mode not in {
        InternetPackage.AccessMode.ALLOWANCE,
        InternetPackage.AccessMode.UNLIMITED,
    }:
        raise ValidationError('المنح الداخلية تدعم رصيد الدقائق أو الوصول غير المحدود فقط.')
    if not validity_value or int(validity_value) <= 0 or not validity_unit:
        raise ValidationError('مدة صلاحية المنحة مطلوبة.')
    if access_mode == InternetPackage.AccessMode.ALLOWANCE:
        if not total_minutes_allowed or int(total_minutes_allowed) <= 0:
            raise ValidationError('إجمالي الدقائق مطلوب لمنحة رصيد الدقائق.')
    elif total_minutes_allowed:
        raise ValidationError('المنحة غير المحدودة لا تستخدم إجمالي دقائق.')
    if int(max_concurrent_devices or 0) < 1 or int(max_registered_devices or 0) < 1:
        raise ValidationError('حد الأجهزة يجب أن يكون واحداً على الأقل.')
    if int(max_concurrent_devices) > int(max_registered_devices):
        raise ValidationError('حد الأجهزة المتزامنة لا يمكن أن يتجاوز الأجهزة المسجلة.')


def _staff_label(user):
    return (user.get_full_name() or user.username or user.phone or '').strip()


def _validate_staff_user(user):
    User = get_user_model()
    provider_role = getattr(User.Role, 'INTERNET_PROVIDER', 'internet_provider')
    if not user or not user.is_active or getattr(user, 'role', '') == provider_role:
        raise ValidationError('اختر حساب موظف فعّالاً من فريق هَبّ.')
    if not user.phone:
        raise ValidationError('حساب الموظف يحتاج رقم هاتف قبل منحه وصول الإنترنت الداخلي.')


@transaction.atomic
def grant_internal_access(*, staff_user=None, member=None, grant_kind, bandwidth_profile, access_mode,
                          validity_value, validity_unit, session_minutes_limit=None,
                          total_minutes_allowed=None, daily_minutes_limit=None,
                          max_concurrent_devices=1, max_registered_devices=1,
                          actor=None, at=None):
    """Grant private complimentary Internet to a Hub staff user.

    ``member`` remains supported for legacy callers while old grants are phased out.
    The staff UI never offers public Member rows for new internal grants.
    """
    origin = GRANT_KIND_TO_ORIGIN.get(grant_kind)
    if not origin:
        raise ValidationError('نوع المنحة الداخلية غير صالح.')

    profile = InternetBandwidthProfile.objects.select_for_update().get(pk=bandwidth_profile.pk)
    if not profile.is_active:
        raise ValidationError('ملف الاتصال المختار غير فعّال.')

    _validate_grant_values(
        access_mode=access_mode,
        validity_value=validity_value,
        validity_unit=validity_unit,
        total_minutes_allowed=total_minutes_allowed,
        max_concurrent_devices=max_concurrent_devices,
        max_registered_devices=max_registered_devices,
    )

    staff_user_id = None
    member_obj = None
    guest_name = ''
    guest_phone = ''
    if staff_user is not None:
        User = get_user_model()
        staff_user = User.objects.select_for_update().get(pk=staff_user.pk)
        _validate_staff_user(staff_user)
        staff_user_id = staff_user.pk
        guest_name = _staff_label(staff_user)
        guest_phone = staff_user.phone
        overlapping = internal_grants().select_for_update().filter(
            member__isnull=True,
            guest_phone=staff_user.phone,
            status__in=(
                InternetEntitlement.Status.PENDING,
                InternetEntitlement.Status.ACTIVE,
                InternetEntitlement.Status.SUSPENDED,
            ),
        ).exists()
        identity_error = 'يوجد بالفعل وصول داخلي قائم لهذا الموظف. ألغِ المنحة الحالية أولاً.'
    elif member is not None:
        member_obj = Member.objects.select_for_update().get(pk=member.pk)
        overlapping = internal_grants().select_for_update().filter(
            member=member_obj,
            status__in=(
                InternetEntitlement.Status.PENDING,
                InternetEntitlement.Status.ACTIVE,
                InternetEntitlement.Status.SUSPENDED,
            ),
        ).exists()
        identity_error = 'يوجد بالفعل وصول داخلي قائم لهذا العضو. ألغِ المنحة الحالية أولاً.'
    else:
        raise ValidationError('حساب الموظف مطلوب للمنحة الداخلية.')

    if overlapping:
        raise ValidationError(identity_error)

    now = at or timezone.now()
    valid_until = validity_end(now, int(validity_value), validity_unit)
    network_backend = 'mikrotik' if settings.MIKROTIK_ENABLED else 'manual'
    entitlement = InternetEntitlement.objects.create(
        package=None,
        member=member_obj,
        guest_name=guest_name,
        guest_phone=guest_phone,
        visit=None,
        order=None,
        payment=None,
        subscription=None,
        origin_type=origin,
        access_mode=access_mode,
        activation_policy=InternetPackage.ActivationPolicy.ON_PURCHASE,
        activated_at=now,
        valid_from=now,
        valid_until=valid_until,
        validity_value=int(validity_value),
        validity_unit=validity_unit,
        session_minutes_limit=(int(session_minutes_limit) if session_minutes_limit else None),
        total_minutes_allowed=(int(total_minutes_allowed) if total_minutes_allowed else None),
        daily_minutes_limit=(int(daily_minutes_limit) if daily_minutes_limit else None),
        bandwidth_profile_code=profile.code,
        max_concurrent_devices=int(max_concurrent_devices),
        max_registered_devices=int(max_registered_devices),
        network_backend=network_backend,
        network_status=InternetEntitlement.NetworkStatus.NOT_PROVISIONED,
        partner=None,
        partner_name_snapshot='',
        partner_share_percent_snapshot=None,
        gross_amount_syp=0,
        created_by=actor,
        status=InternetEntitlement.Status.ACTIVE,
    )
    ActivityLog.objects.create(
        actor=actor,
        action='internet.internal_access_granted',
        details={
            'entitlement_id': entitlement.pk,
            'staff_user_id': staff_user_id,
            'member_id': member_obj.pk if member_obj else None,
            'grant_kind': grant_kind,
            'bandwidth_profile_code': profile.code,
            'access_mode': access_mode,
            'valid_until': valid_until.isoformat() if valid_until else None,
            'max_concurrent_devices': entitlement.max_concurrent_devices,
            'max_registered_devices': entitlement.max_registered_devices,
        },
    )
    enqueue_network_operation(
        entitlement,
        InternetNetworkOperation.Operation.PROVISION,
        reason='internal owner/team Internet grant',
        idempotency_key=f'entitlement:{entitlement.public_code}:internal-grant:provision',
    )
    return entitlement


@transaction.atomic
def revoke_internal_access(entitlement, *, actor=None):
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if entitlement.origin_type not in INTERNAL_ORIGINS:
        raise ValidationError('هذا الاستحقاق ليس منحة إنترنت داخلية.')
    if entitlement.status in {
        InternetEntitlement.Status.CANCELLED,
        InternetEntitlement.Status.EXPIRED,
    }:
        return entitlement
    entitlement = cancel_internet_entitlement(
        entitlement,
        actor=actor,
        reason='internal_access_revoked',
    )
    ActivityLog.objects.create(
        actor=actor,
        action='internet.internal_access_revoked',
        details={
            'entitlement_id': entitlement.pk,
            'member_id': entitlement.member_id,
            'staff_phone_snapshot': entitlement.guest_phone if not entitlement.member_id else '',
            'origin_type': entitlement.origin_type,
        },
    )
    return entitlement
