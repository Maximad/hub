"""Device-specific customer Internet orchestration for shared Hub visits.

HubVisit remains the shared commercial bill.  A HubVisitBrowserCredential identifies
one browser/device that selected or joined that bill, and each new self-service
InternetSession is bound to that credential through InternetSessionBrowserBinding.

Historical sessions created before this feature may be unbound.  They are treated as
visit-wide legacy sessions until they end so a deployment cannot strand live access.
"""
import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import ActivityLog, HubVisit, InternetEntitlement, InternetPackage, InternetSession, Payment
from core.services.internet_access import create_commercial_sale, start_usage_session
from core.services.visit_internet import (
    _apply_customer_bandwidth_profile,
    _customer_bandwidth_profile_code,
    _metered_network_provider,
    authorize_entitlement,
    metered_customer_error,
    package_customer_error,
)
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionBrowserBinding, InternetSessionNetworkOperation
from internet.session_network_backends import NOT_PROVISIONED
from internet.session_network_operations import enqueue_session_network_operation


def browser_session_queryset(credential, *, include_legacy=True):
    """Return Internet sessions this browser may see/control within its visit.

    Unbound rows are pre-feature legacy sessions.  Keeping them visible preserves the
    former visit-wide behavior only for those historical rows; every new customer
    self-service session is bound and therefore isolated to its initiating browser.
    """
    if not credential:
        return InternetSession.objects.none()
    queryset = InternetSession.objects.filter(visit_id=credential.visit_id)
    owned = Q(browser_binding__credential_id=credential.pk)
    if include_legacy:
        owned |= Q(browser_binding__isnull=True)
    return queryset.filter(owned)


def active_browser_session(credential):
    return (
        browser_session_queryset(credential)
        .filter(status=InternetSession.Status.ACTIVE)
        .select_related('package', 'entitlement')
        .order_by('-start_time', '-pk')
        .first()
    )


def bind_session_to_credential(session, credential):
    """Bind exactly one customer Internet session to its initiating browser."""
    if not credential or session.visit_id != credential.visit_id or not session.visit_id:
        raise ValidationError('تعذر ربط جلسة الإنترنت بهذا الجهاز.')
    binding = InternetSessionBrowserBinding.objects.filter(session=session).first()
    if binding:
        if binding.credential_id != credential.pk:
            raise ValidationError('جلسة الإنترنت مرتبطة بجهاز آخر.')
        return binding
    return InternetSessionBrowserBinding.objects.create(
        session=session,
        credential=credential,
    )


def _validate_open_visit_credential(visit, credential):
    if not credential or credential.visit_id != visit.pk or visit.status != HubVisit.Status.OPEN:
        raise ValidationError('الجلسة مغلقة.')


def _device_sale_key(credential, request_key):
    if not request_key or len(request_key) > 200:
        raise ValidationError('رمز المحاولة غير صالح.')
    payload = f'{credential.pk}:{request_key}'.encode('utf-8')
    return 'visit-device:' + hashlib.sha256(payload).hexdigest()


def _active_for_device(visit, credential):
    return (
        browser_session_queryset(credential)
        .filter(visit=visit, status=InternetSession.Status.ACTIVE)
        .select_related('entitlement')
        .order_by('-start_time', '-pk')
        .first()
    )


@transaction.atomic
def create_visit_internet_sale_and_start(*, visit, credential, package, request_key,
                                         member=None, actor=None, at=None):
    """Purchase/start one package for this browser while sharing the visit bill."""
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    _validate_open_visit_credential(visit, credential)
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    error = package_customer_error(package, member, at)
    if error:
        raise ValidationError(error)

    sale_key = _device_sale_key(credential, request_key)
    active_for_device = _active_for_device(visit, credential)
    if active_for_device:
        if (active_for_device.entitlement_id
                and active_for_device.entitlement.idempotency_key == sale_key):
            return active_for_device.entitlement, _apply_customer_bandwidth_profile(active_for_device), False
        raise ValidationError('لديك جلسة إنترنت فعالة على هذا الجهاز. أنهِها قبل بدء باقة أخرى.')

    entitlement = create_commercial_sale(
        package,
        payment_method=Payment.Method.UNPAID,
        member=member,
        actor=actor,
        idempotency_key=sale_key,
        visit=visit,
    )
    if entitlement.visit_id != visit.pk or (
            entitlement.order_id and entitlement.order.visit_id != visit.pk):
        raise ValidationError('استُخدم رمز المحاولة لجلسة مختلفة.')

    active = entitlement.sessions.filter(status=InternetSession.Status.ACTIVE).first()
    if active:
        if active.visit_id != visit.pk:
            raise ValidationError('لديك جلسة إنترنت فعالة بالفعل.')
        bind_session_to_credential(active, credential)
        return entitlement, _apply_customer_bandwidth_profile(active), False

    session = _apply_customer_bandwidth_profile(
        start_usage_session(entitlement, actor=actor, at=at, visit=visit)
    )
    bind_session_to_credential(session, credential)
    now = at or timezone.now()
    HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=now)
    ActivityLog.objects.create(action='visit.internet_sale_created', details={
        'visit_id': visit.pk,
        'order_id': entitlement.order_id,
        'entitlement_id': entitlement.pk,
        'session_id': session.pk,
    })
    ActivityLog.objects.create(action='visit.internet_session_started', details={
        'visit_id': visit.pk,
        'session_id': session.pk,
        'entitlement_id': entitlement.pk,
    })
    return entitlement, session, True


@transaction.atomic
def start_visit_metered_session(*, visit, credential, member=None, guest_phone='', actor=None, at=None):
    """Start/reuse this browser's package-less metered session on a shared bill."""
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    _validate_open_visit_credential(visit, credential)

    settings_obj = get_system_settings()
    error = metered_customer_error(settings_obj, member)
    if error:
        raise ValidationError(error)
    if member is None and settings_obj.require_phone_for_guest_session and not (guest_phone or '').strip():
        raise ValidationError('رقم الهاتف مطلوب لبدء الإنترنت.')

    active = _active_for_device(visit, credential)
    if active:
        if (active.entitlement_id is None and active.package_id is None
                and active.billing_mode == InternetSession.BillingMode.OPEN_METERED):
            return _apply_customer_bandwidth_profile(active), False
        raise ValidationError('لديك جلسة إنترنت فعالة على هذا الجهاز. أنهِها قبل بدء جلسة أخرى.')

    requested = at or timezone.now()
    network_provider = _metered_network_provider()
    session = InternetSession.objects.create(
        session_type=InternetSession.SessionType.INTERNET,
        member=member,
        visit=visit,
        package=None,
        entitlement=None,
        guest_phone=(guest_phone or '').strip(),
        customer_phone=(guest_phone or '').strip(),
        billing_mode=InternetSession.BillingMode.OPEN_METERED,
        started_at=requested,
        start_time=requested,
        rate_per_hour_syp=int(settings_obj.default_rate_per_hour_syp or 0),
        minimum_minutes=int(settings_obj.default_minimum_minutes or 0),
        free_grace_minutes=int(settings_obj.default_free_grace_minutes or 0),
        rounding_increment_minutes=int(settings_obj.default_rounding_increment_minutes or 15),
        minimum_charge_syp=int(settings_obj.default_minimum_charge_syp or 0),
        daily_cap_syp=settings_obj.default_daily_cap_syp,
        notes='بدء ذاتي — جلسة إنترنت حسب الوقت لهذا الجهاز',
        status=InternetSession.Status.ACTIVE,
        started_by=actor,
        bandwidth_profile=(
            _customer_bandwidth_profile_code()
            if network_provider == InternetSession.NetworkProvider.MIKROTIK
            else ''
        ),
        network_provider=network_provider,
        network_status=NOT_PROVISIONED,
    )
    bind_session_to_credential(session, credential)
    enqueue_session_network_operation(
        session,
        InternetSessionNetworkOperation.Operation.PROVISION,
        reason='customer device metered Internet start',
        process_after_commit=False,
    )
    HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=requested)
    ActivityLog.objects.create(actor=actor, action='visit.internet_metered_requested', details={
        'visit_id': visit.pk,
        'session_id': session.pk,
        'rate_per_hour_syp': session.rate_per_hour_syp,
        'network_provider': session.network_provider,
        'bandwidth_profile': session.bandwidth_profile,
    })
    return session, True


@transaction.atomic
def start_existing_visit_entitlement(*, visit, credential, entitlement, actor=None, at=None):
    """Start a shared/member entitlement on this browser, respecting its own limits."""
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    _validate_open_visit_credential(visit, credential)
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    authorize_entitlement(visit, entitlement, at)

    active_for_device = _active_for_device(visit, credential)
    if active_for_device:
        if active_for_device.entitlement_id == entitlement.pk:
            return _apply_customer_bandwidth_profile(active_for_device), False
        raise ValidationError('لديك جلسة إنترنت فعالة على هذا الجهاز. أنهِها قبل بدء جلسة أخرى.')

    # Other browser-bound sessions on the same visit do not block this device.
    # The generic entitlement engine remains authoritative for timed one-shot and
    # max_concurrent_devices limits on a shared/member entitlement.
    session = _apply_customer_bandwidth_profile(
        start_usage_session(entitlement, actor=actor, at=at, visit=visit)
    )
    bind_session_to_credential(session, credential)
    ActivityLog.objects.create(action='visit.internet_session_started', details={
        'visit_id': visit.pk,
        'session_id': session.pk,
        'entitlement_id': entitlement.pk,
    })
    return session, True
