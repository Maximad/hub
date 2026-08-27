"""Customer Internet orchestration layered on the existing commercial engine."""
import hashlib

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.internet_billing import finalize_internet_session
from core.models import (ActivityLog, HubVisit, InternetEntitlement, InternetPackage,
                         InternetSession, Order, OrderItem, Payment)
from core.services.internet_access import (create_commercial_sale, effectively_active_entitlements,
                                           start_usage_session)
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionNetworkOperation, InternetSessionNetworkState
from internet.session_network_backends import NOT_PROVISIONED
from internet.session_network_operations import (enqueue_session_network_operation,
                                                  process_session_network_operation)
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


def metered_customer_error(system_settings, member=None):
    """Validate the package-less customer metered path without creating anything."""
    if not self_service_enabled(system_settings):
        return 'خدمة الإنترنت الذاتية غير متاحة حالياً.'
    if not system_settings.internet_metered_enabled:
        return 'الإنترنت حسب الوقت غير متاح حالياً.'
    if member is None and not system_settings.allow_guest_internet_sessions:
        return 'جلسات الإنترنت للزوار غير متاحة حالياً.'
    if member is not None and not system_settings.allow_member_internet_sessions:
        return 'جلسات الإنترنت للأعضاء غير متاحة حالياً.'
    if int(system_settings.default_rate_per_hour_syp or 0) <= 0:
        return 'سعر الإنترنت حسب الوقت غير مضبوط حالياً.'
    if not system_settings.auto_create_order_for_metered_sessions:
        return 'الفوترة التلقائية للإنترنت حسب الوقت غير مفعلة.'
    if not system_settings.internet_service_product_id:
        return 'منتج خدمة الإنترنت غير مضبوط حالياً.'
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


def _other_active_visit_session_exists(visit, *, entitlement_id=None, idempotency_key=None):
    sessions = InternetSession.objects.filter(
        visit=visit,
        status=InternetSession.Status.ACTIVE,
    )
    if entitlement_id is not None:
        sessions = sessions.exclude(entitlement_id=entitlement_id)
    if idempotency_key is not None:
        sessions = sessions.exclude(entitlement__idempotency_key=idempotency_key)
    return sessions.exists()


def _metered_network_provider():
    return (InternetSession.NetworkProvider.MIKROTIK
            if getattr(django_settings, 'MIKROTIK_ENABLED', False)
            else InternetSession.NetworkProvider.MANUAL)


def _customer_bandwidth_profile_code():
    return (
        getattr(django_settings, 'MIKROTIK_CUSTOMER_PROFILE_CODE', 'fast')
        or 'fast'
    ).strip()


def _apply_customer_bandwidth_profile(session):
    """Use the explicit customer profile when a MikroTik session has none.

    Package/session-specific profiles remain authoritative. This only prevents a
    customer-started Internet session from silently falling back to the safe slow
    RouterOS default.
    """
    if (session.network_provider == InternetSession.NetworkProvider.MIKROTIK
            and not session.bandwidth_profile):
        session.bandwidth_profile = _customer_bandwidth_profile_code()
        session.save(update_fields=['bandwidth_profile', 'updated_at'])
    return session


def metered_network_activated_at(session):
    state = InternetSessionNetworkState.objects.filter(session_id=session.pk).first()
    return state.network_activated_at if state else None


def prepare_visit_metered_session_network(session):
    """Give a customer metered session one immediate, durable provision attempt.

    The caller must invoke this outside the transaction that created the session.
    Failed provisioning leaves the commercial session unbilled; the durable job is
    retained for the normal retry worker.
    """
    if metered_network_activated_at(session) is not None:
        return True
    job = (
        InternetSessionNetworkOperation.objects.filter(
            session=session,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
        )
        .order_by('created_at')
        .first()
    )
    if job is None:
        job = enqueue_session_network_operation(
            session,
            InternetSessionNetworkOperation.Operation.PROVISION,
            reason='customer metered Internet start',
            process_after_commit=False,
        )
    process_session_network_operation(job)
    session.refresh_from_db()
    return metered_network_activated_at(session) is not None


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
    sale_key = _sale_key(request_key)
    # A customer visit has one live network session at a time. Preserve retries of
    # the same sale key, but reject a second package before creating another charge.
    if _other_active_visit_session_exists(visit, idempotency_key=sale_key):
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل. أنهِها قبل بدء باقة أخرى.')
    entitlement = create_commercial_sale(
        package, payment_method=Payment.Method.UNPAID, member=member,
        actor=actor, idempotency_key=sale_key, visit=visit,
    )
    if entitlement.visit_id != visit.pk or (entitlement.order_id and entitlement.order.visit_id != visit.pk):
        raise ValidationError('استُخدم رمز المحاولة لجلسة مختلفة.')
    active = entitlement.sessions.filter(status=InternetSession.Status.ACTIVE).first()
    if active:
        if active.visit_id == visit.pk:
            return entitlement, _apply_customer_bandwidth_profile(active), False
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل.')
    session = _apply_customer_bandwidth_profile(
        start_usage_session(entitlement, actor=actor, at=at, visit=visit)
    )
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
def start_visit_metered_session(*, visit, credential, member=None, guest_phone='', actor=None, at=None):
    """Request the default package-less, time-metered Internet session for a visit.

    The billing timestamp written here is provisional. The session network worker
    replaces it exactly once after successful network provisioning and records the
    durable ``network_activated_at`` billing gate.
    """
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    if not credential or credential.visit_id != visit.pk or visit.status != HubVisit.Status.OPEN:
        raise ValidationError('الجلسة مغلقة.')

    settings_obj = get_system_settings()
    error = metered_customer_error(settings_obj, member)
    if error:
        raise ValidationError(error)
    if member is None and settings_obj.require_phone_for_guest_session and not (guest_phone or '').strip():
        raise ValidationError('رقم الهاتف مطلوب لبدء الإنترنت.')

    active = InternetSession.objects.select_for_update().filter(
        visit=visit, status=InternetSession.Status.ACTIVE,
    ).order_by('-start_time', '-pk').first()
    if active:
        # A repeated tap/retry, including another browser at the same table, reuses
        # the already-running or still-provisioning package-less metered session.
        if (active.entitlement_id is None and active.package_id is None and
                active.billing_mode == InternetSession.BillingMode.OPEN_METERED):
            return _apply_customer_bandwidth_profile(active), False
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل. أنهِها قبل بدء جلسة أخرى.')

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
        notes='بدء ذاتي من QR الطاولة — جلسة إنترنت حسب الوقت',
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
    enqueue_session_network_operation(
        session,
        InternetSessionNetworkOperation.Operation.PROVISION,
        reason='customer metered Internet start',
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


def _metered_order_note(session):
    customer = session.member.name_ar if session.member_id else (session.display_guest_name or 'زائر')
    return (
        f'جلسة إنترنت حسب الوقت #{session.pk}\n'
        f'الوقت الفعلي: {session.effective_duration_minutes or 0} دقيقة\n'
        f'الوقت المحسوب: {session.billable_minutes or 0} دقيقة\n'
        f'العميل: {customer}'
    )


def _enqueue_metered_disconnect(session, reason):
    return enqueue_session_network_operation(
        session,
        InternetSessionNetworkOperation.Operation.DISCONNECT,
        reason=reason,
        process_after_commit=True,
    )


@transaction.atomic
def finalize_visit_metered_session(session, *, actor=None, at=None):
    """End a package-less metered session and materialize its charge on the visit."""
    # Nullable provenance relations are useful for the billing/order work below,
    # but PostgreSQL cannot apply FOR UPDATE to the nullable side of their outer
    # joins. Lock the InternetSession row only, mirroring the other Internet locks.
    session = (
        InternetSession.objects.select_related(
            'visit', 'visit__table', 'visit__member', 'member', 'linked_order',
        )
        .select_for_update(of=('self',))
        .get(pk=session.pk)
    )
    if session.entitlement_id:
        raise ValidationError('هذه الجلسة مرتبطة بباقة وليست جلسة محسوبة حسب الوقت.')
    if session.status != InternetSession.Status.ACTIVE:
        return session

    ended_at = at or timezone.now()
    # For network-managed customer sessions the durable activation marker is the
    # authority that billing ever began. A failed provisioning attempt can always
    # be stopped/cancelled with a zero charge.
    if (session.network_provider == InternetSession.NetworkProvider.MIKROTIK
            and metered_network_activated_at(session) is None):
        session.ended_at = ended_at
        session.end_time = ended_at
        session.duration_minutes = 0
        session.actual_duration_minutes = 0
        session.billable_minutes = 0
        session.calculated_total_syp = 0
        session.status = InternetSession.Status.CANCELLED
        session.lifecycle_end_reason = 'network_not_activated'
        session.cancellation_reason = 'لم يكتمل تجهيز الشبكة؛ أُلغيت الجلسة دون احتساب وقت.'
        session.ended_by = actor if getattr(actor, 'is_authenticated', False) else None
        session.save(update_fields=[
            'ended_at', 'end_time', 'duration_minutes', 'actual_duration_minutes',
            'billable_minutes', 'calculated_total_syp', 'status', 'lifecycle_end_reason',
            'cancellation_reason', 'ended_by', 'updated_at',
        ])
        _enqueue_metered_disconnect(session, 'cancel unactivated metered session')
        ActivityLog.objects.create(actor=actor, action='visit.internet_metered_cancelled_unprovisioned', details={
            'visit_id': session.visit_id,
            'session_id': session.pk,
        })
        if session.visit_id:
            HubVisit.objects.filter(pk=session.visit_id).update(last_activity_at=ended_at)
        return session

    settings_obj = get_system_settings()
    session = finalize_internet_session(session, actor, ended_at=ended_at)
    total = int(session.payable_total_syp or 0)

    if total > 0:
        if not settings_obj.auto_create_order_for_metered_sessions:
            raise ValidationError('الفوترة التلقائية للإنترنت حسب الوقت غير مفعلة.')
        product = settings_obj.internet_service_product
        if product is None:
            raise ValidationError('منتج خدمة الإنترنت غير مضبوط حالياً.')
        if not session.visit_id:
            raise ValidationError('تعذر ربط جلسة الإنترنت بزيارة الزبون.')

        if session.linked_order_id:
            order = session.linked_order
        else:
            visit = session.visit
            note = _metered_order_note(session)
            order = Order.objects.create(
                table=visit.table,
                visit=visit,
                member=visit.member,
                service_mode=(Order.ServiceMode.TABLE if visit.table_id else Order.ServiceMode.DINE_IN),
                fulfillment_mode=(Order.FulfillmentMode.TABLE if visit.table_id else Order.FulfillmentMode.INSIDE_SPACE),
                status=Order.Status.NEW,
                notes=note,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=1,
                product_name_ar_snapshot=product.name_ar,
                product_name_en_snapshot=product.name_en,
                unit_price_syp_snapshot=total,
                selected_options_snapshot=[],
                item_note=note,
                line_total_syp_snapshot=total,
                prep_status=OrderItem.PrepStatus.NO_PREP,
            )
            session.linked_order = order
            session.status = InternetSession.Status.BILLED
            session.save(update_fields=['linked_order', 'status', 'updated_at'])
            ActivityLog.objects.create(actor=actor, action='visit.internet_metered_billed', details={
                'visit_id': visit.pk,
                'session_id': session.pk,
                'order_id': order.pk,
                'amount_syp': total,
            })

    _enqueue_metered_disconnect(session, 'metered session ended')
    if session.visit_id:
        HubVisit.objects.filter(pk=session.visit_id).update(last_activity_at=ended_at)
    return session


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
            return _apply_customer_bandwidth_profile(active), False
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل.')
    if _other_active_visit_session_exists(visit, entitlement_id=entitlement.pk):
        raise ValidationError('لديك جلسة إنترنت فعالة بالفعل. أنهِها قبل بدء باقة أخرى.')
    session = _apply_customer_bandwidth_profile(
        start_usage_session(entitlement, actor=actor, at=at, visit=visit)
    )
    ActivityLog.objects.create(action='visit.internet_session_started', details={
        'visit_id': visit.pk, 'session_id': session.pk, 'entitlement_id': entitlement.pk,
    })
    return session, True
