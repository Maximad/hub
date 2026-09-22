from django import template
from django.db.models import Q
from django.utils import timezone

from core.models import (
    ActivityLog,
    InternetNetworkOperation,
    InternetSession,
)
from core.services.internet_readiness import mikrotik_check_is_fresh, worker_is_fresh
from internet.models import InternetSessionNetworkOperation


register = template.Library()


def _provider_session_scope(partner):
    scope = Q(entitlement__partner=partner)
    if partner.is_default:
        scope |= Q(
            entitlement__isnull=True,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
        )
    return scope


def _provider_session_operation_scope(partner):
    scope = Q(session__entitlement__partner=partner)
    if partner.is_default:
        scope |= Q(
            session__entitlement__isnull=True,
            session__network_provider=InternetSession.NetworkProvider.MIKROTIK,
        )
    return scope


def _safe_operation_error(value):
    text = (value or '').replace('\r', ' ').replace('\n', ' ').strip()
    if not text:
        return '—'
    lowered = text.lower()
    if any(marker in lowered for marker in (
        'tls', 'certificate', 'شهادة', 'timeout', 'connection', 'اتصال',
    )):
        return 'تعذر الاتصال بالشبكة أو التحقق من TLS.'
    if any(marker in lowered for marker in (
        'authentication', 'authorization', 'credential', 'اعتماد', '401', '403',
    )):
        return 'تعذر إكمال العملية باستخدام حساب الخدمة الحالي.'
    if any(marker in lowered for marker in (
        'profile', 'routeros', 'hotspot', 'ملف', 'مورد',
    )):
        return 'تعذر استخدام مورد الشبكة المطلوب لهذه العملية.'
    return 'فشلت عملية الشبكة؛ راجع نوع العملية ووقت الفشل قبل إعادة المحاولة.'


def _operation_summary(entitlement_operations, session_operations):
    pending_statuses = ('pending', 'processing')
    failed_entitlement = entitlement_operations.filter(
        status=InternetNetworkOperation.Status.FAILED,
    )
    failed_session = session_operations.filter(
        status=InternetSessionNetworkOperation.Status.FAILED,
    )
    pending_total = (
        entitlement_operations.filter(status__in=pending_statuses).count()
        + session_operations.filter(status__in=pending_statuses).count()
    )
    failed_total = failed_entitlement.count() + failed_session.count()

    candidates = []
    entitlement_failure = failed_entitlement.order_by('-updated_at').first()
    if entitlement_failure:
        candidates.append({
            'kind': 'entitlement',
            'operation': entitlement_failure.get_operation_display(),
            'updated_at': entitlement_failure.updated_at,
            'safe_error': _safe_operation_error(entitlement_failure.last_error),
        })
    session_failure = failed_session.order_by('-updated_at').first()
    if session_failure:
        candidates.append({
            'kind': 'session',
            'operation': session_failure.get_operation_display(),
            'updated_at': session_failure.updated_at,
            'safe_error': _safe_operation_error(session_failure.last_error),
        })
    latest_failure = max(candidates, key=lambda item: item['updated_at']) if candidates else None
    return {
        'pending_total': pending_total,
        'failed_total': failed_total,
        'latest_failure': latest_failure,
    }


@register.simple_tag
def provider_network_visibility(partner, operations_state=None):
    """Return provider-scoped, secret-free operational truth for the network page.

    Hub session rows, network-ready rows, and currently connected devices are
    deliberately different concepts. This helper never treats a stored session as
    proof that a device is currently online.
    """
    now = timezone.now()
    sessions = InternetSession.objects.filter(_provider_session_scope(partner))
    active_sessions = sessions.filter(status=InternetSession.Status.ACTIVE)

    ready_total = 0
    pending_total = 0
    failed_total = 0
    for session in active_sessions.select_related('entitlement'):
        network_status = (
            session.entitlement.network_status
            if session.entitlement_id
            else session.network_status
        )
        if network_status == 'provisioned':
            ready_total += 1
        elif network_status in {'provision_error', 'failed'}:
            failed_total += 1
        else:
            pending_total += 1

    entitlement_operations = InternetNetworkOperation.objects.filter(
        entitlement__partner=partner,
    )
    session_operations = InternetSessionNetworkOperation.objects.filter(
        _provider_session_operation_scope(partner),
    ).select_related('session', 'session__member', 'session__entitlement')
    operations = _operation_summary(entitlement_operations, session_operations)

    recent_session_operations = []
    for operation in session_operations.order_by('-updated_at')[:20]:
        session = operation.session
        recent_session_operations.append({
            'session': session,
            'customer': (
                session.member.name_ar
                if session.member_id
                else (session.display_guest_name or 'زائر')
            ),
            'operation': operation.get_operation_display(),
            'status': operation.status,
            'status_label': operation.get_status_display(),
            'attempt_count': operation.attempt_count,
            'updated_at': operation.updated_at,
            'safe_error': (
                _safe_operation_error(operation.last_error)
                if operation.status == InternetSessionNetworkOperation.Status.FAILED
                else ''
            ),
        })

    check_at = operations_state.last_mikrotik_check_at if operations_state else None
    check_ok = operations_state.last_mikrotik_check_ok if operations_state else None
    check_fresh = mikrotik_check_is_fresh(operations_state, at=now)
    if not check_at or check_ok is None:
        router = {'code': 'unknown', 'label': 'غير معروف'}
    elif check_ok and check_fresh:
        router = {'code': 'ok', 'label': 'قراءة ناجحة حديثاً'}
    elif check_ok:
        router = {'code': 'stale', 'label': 'آخر قراءة ناجحة قديمة'}
    else:
        router = {'code': 'failed', 'label': 'فشل آخر فحص قراءة'}
    router['last_check_at'] = check_at
    last_success = ActivityLog.objects.filter(
        action='internet.mikrotik_readonly_healthcheck',
        details__ok=True,
    ).order_by('-created_at').first()
    router['last_success_at'] = last_success.created_at if last_success else None

    heartbeat = operations_state.last_worker_seen_at if operations_state else None
    if not heartbeat:
        worker = {'code': 'unknown', 'label': 'لا توجد نبضة مسجلة'}
    elif worker_is_fresh(operations_state, at=now):
        worker = {'code': 'ok', 'label': 'العامل حديث'}
    else:
        worker = {'code': 'stale', 'label': 'العامل متأخر'}
    worker['last_seen_at'] = heartbeat

    return {
        'hub_active_sessions': active_sessions.count(),
        'network_ready_sessions': ready_total,
        'network_pending_sessions': pending_total,
        'network_failed_sessions': failed_total,
        'connected_devices_measured': False,
        'router': router,
        'worker': worker,
        'operations': operations,
        'recent_session_operations': recent_session_operations,
    }
