"""Customer Internet orchestration layered on the existing commercial engine."""
import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (ActivityLog, HubVisit, InternetEntitlement, InternetPackage,
                         InternetSession, Payment)
from core.services.internet_access import (create_commercial_sale, effectively_active_entitlements,
                                           start_usage_session)
from members.benefits import is_member_eligible_for_internet_package


def self_service_enabled(system_settings):
    return bool(system_settings.customer_visits_enabled and
                system_settings.customer_internet_self_service_enabled)


def package_customer_error(package, member=None, at=None):
    """Return a safe Arabic rejection, or ``None`` when customer-startable."""
    if not package.is_active or not package.visible_to_customer:
        return 'الباقة غير متاحة.'
    if package.activation_policy == InternetPackage.ActivationPolicy.MANUAL:
        return 'الباقة غير متاحة للبدء الذاتي.'
    if package.access_mode == InternetPackage.AccessMode.MEMBERSHIP_CREDIT:
        return 'الباقة غير متاحة للشراء.'
    eligible_member = is_member_eligible_for_internet_package(member, package, at)
    if package.member_only and not eligible_member:
        return 'هذه الباقة مخصصة للأعضاء.'
    if member is None and not package.guest_allowed:
        return 'هذه الباقة مخصصة للأعضاء.'
    return None


def customer_packages(member=None, at=None):
    packages = InternetPackage.objects.filter(
        is_active=True, visible_to_customer=True,
    ).exclude(activation_policy=InternetPackage.ActivationPolicy.MANUAL).exclude(
        access_mode=InternetPackage.AccessMode.MEMBERSHIP_CREDIT,
    ).order_by('sort_order', 'pk')
    return [package for package in packages if package_customer_error(package, member, at) is None]


def usable_member_entitlements(visit, at=None):
    if not visit.member_id:
        return InternetEntitlement.objects.none()
    return effectively_active_entitlements(
        InternetEntitlement.objects.filter(member_id=visit.member_id), at=at,
    ).exclude(activation_policy=InternetPackage.ActivationPolicy.MANUAL)


def authorize_entitlement(visit, entitlement, at=None):
    direct = entitlement.visit_id == visit.pk
    member_owned = bool(visit.member_id and entitlement.member_id == visit.member_id and
                        entitlement.effective_status(at) == entitlement.Status.ACTIVE)
    if not (direct or member_owned):
        raise ValidationError('الباقة غير متاحة.')
    if entitlement.effective_status(at) != entitlement.Status.ACTIVE:
        raise ValidationError('انتهت صلاحية هذه الباقة.')


def _sale_key(request_key):
    if not request_key or len(request_key) > 200:
        raise ValidationError('رمز المحاولة غير صالح.')
    return 'visit:' + hashlib.sha256(request_key.encode('utf-8')).hexdigest()


@transaction.atomic
def create_visit_internet_sale_and_start(*, visit, credential, package, request_key,
                                         member=None, actor=None, at=None):
    """Atomically charge an open visit and start (or reuse) its purchased session."""
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    if not credential or credential.visit_id != visit.pk or visit.status != HubVisit.Status.OPEN:
        raise ValidationError('الجلسة مغلقة.')
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    error = package_customer_error(package, member, at)
    if error:
        raise ValidationError(error)
    entitlement = create_commercial_sale(
        package, payment_method=Payment.Method.UNPAID, member=member,
        actor=actor, idempotency_key=_sale_key(request_key), visit=visit,
    )
    if entitlement.visit_id != visit.pk or (entitlement.order_id and entitlement.order.visit_id != visit.pk):
        raise ValidationError('استُخدم رمز المحاولة لجلسة مختلفة.')
    active = entitlement.sessions.filter(status=InternetSession.Status.ACTIVE).first()
    if active:
        if active.visit_id == visit.pk:
            return entitlement, active, False
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل.')
    session = start_usage_session(entitlement, actor=actor, at=at, visit=visit)
    now = at or timezone.now()
    HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=now)
    ActivityLog.objects.create(action='visit.internet_sale_created', details={
        'visit_id': visit.pk, 'order_id': entitlement.order_id,
        'entitlement_id': entitlement.pk,
    })
    ActivityLog.objects.create(action='visit.internet_session_started', details={
        'visit_id': visit.pk, 'session_id': session.pk, 'entitlement_id': entitlement.pk,
    })
    return entitlement, session, True


@transaction.atomic
def start_existing_visit_entitlement(*, visit, credential, entitlement, actor=None, at=None):
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    if not credential or credential.visit_id != visit.pk or visit.status != HubVisit.Status.OPEN:
        raise ValidationError('الجلسة مغلقة.')
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    authorize_entitlement(visit, entitlement, at)
    active = entitlement.sessions.filter(status=InternetSession.Status.ACTIVE).first()
    if active:
        if active.visit_id == visit.pk:
            return active, False
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل.')
    session = start_usage_session(entitlement, actor=actor, at=at, visit=visit)
    ActivityLog.objects.create(action='visit.internet_session_started', details={
        'visit_id': visit.pk, 'session_id': session.pk, 'entitlement_id': entitlement.pk,
    })
    return session, True
