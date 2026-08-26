from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.internet_integrity import access_integrity_findings, commercial_integrity_findings
from core.models import InternetBandwidthProfile, InternetNetworkOperation, InternetPackage, InternetPartner
from internet.models import InternetOperationsState, InternetSessionNetworkOperation


WORKER_FRESH_FOR = timedelta(seconds=30)
MIKROTIK_CHECK_FRESH_FOR = timedelta(minutes=10)


def get_operations_state(*, create=False):
    if create:
        return InternetOperationsState.objects.get_or_create(key='default')[0]
    return InternetOperationsState.objects.filter(key='default').first()


def worker_is_fresh(state=None, *, at=None):
    state = state or get_operations_state()
    now = at or timezone.now()
    return bool(
        state
        and state.last_worker_seen_at
        and state.last_worker_seen_at >= now - WORKER_FRESH_FOR
    )


def mikrotik_check_is_fresh(state=None, *, at=None):
    state = state or get_operations_state()
    now = at or timezone.now()
    return bool(
        state
        and state.last_mikrotik_check_at
        and state.last_mikrotik_check_at >= now - MIKROTIK_CHECK_FRESH_FOR
    )


def _hotspot_login_url_ok():
    value = (getattr(settings, 'MIKROTIK_HOTSPOT_LOGIN_URL', '') or '').strip()
    if not value:
        return False
    parsed = urlsplit(value)
    return bool(
        parsed.scheme == 'https'
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def mikrotik_static_configured():
    base_url = (getattr(settings, 'MIKROTIK_BASE_URL', '') or '').strip()
    return all((
        base_url.startswith('https://'),
        bool(getattr(settings, 'MIKROTIK_USERNAME', '')),
        bool(getattr(settings, 'MIKROTIK_PASSWORD', '')),
        bool(getattr(settings, 'MIKROTIK_HOTSPOT_SERVER', '')),
        bool(getattr(settings, 'MIKROTIK_DEFAULT_PROFILE', '')),
        bool(getattr(settings, 'MIKROTIK_CREDENTIAL_KEY', '')),
        bool(getattr(settings, 'MIKROTIK_VERIFY_TLS', False)),
        _hotspot_login_url_ok(),
    ))


def mikrotik_enablement_preflight(*, at=None):
    """Return a secret-free checklist for deciding whether router enablement is safe."""
    now = at or timezone.now()
    state = get_operations_state()
    entitlement_failed = InternetNetworkOperation.objects.filter(
        status=InternetNetworkOperation.Status.FAILED,
    ).count()
    session_failed = InternetSessionNetworkOperation.objects.filter(
        status=InternetSessionNetworkOperation.Status.FAILED,
    ).count()
    base_url = (getattr(settings, 'MIKROTIK_BASE_URL', '') or '').strip()
    last_check_fresh = mikrotik_check_is_fresh(state, at=now)
    last_check_ok = bool(state and state.last_mikrotik_check_ok is True and last_check_fresh)

    checks = [
        {
            'code': 'base_url', 'label': 'عنوان RouterOS REST يستخدم HTTPS',
            'ok': base_url.startswith('https://'), 'blocking': True,
        },
        {
            'code': 'service_account', 'label': 'حساب خدمة MikroTik مضبوط في البيئة',
            'ok': bool(getattr(settings, 'MIKROTIK_USERNAME', '') and getattr(settings, 'MIKROTIK_PASSWORD', '')),
            'blocking': True,
        },
        {
            'code': 'tls_verify', 'label': 'التحقق من شهادة TLS مفعّل',
            'ok': bool(getattr(settings, 'MIKROTIK_VERIFY_TLS', False)), 'blocking': True,
        },
        {
            'code': 'hotspot_server', 'label': 'اسم خادم HotSpot مضبوط',
            'ok': bool(getattr(settings, 'MIKROTIK_HOTSPOT_SERVER', '')), 'blocking': True,
        },
        {
            'code': 'hotspot_login_url', 'label': 'رابط تسجيل HotSpot آمن وصالح',
            'ok': _hotspot_login_url_ok(), 'blocking': True,
        },
        {
            'code': 'default_profile', 'label': 'ملف RouterOS الافتراضي مضبوط',
            'ok': bool(getattr(settings, 'MIKROTIK_DEFAULT_PROFILE', '')), 'blocking': True,
        },
        {
            'code': 'credential_key', 'label': 'مفتاح تشفير بيانات الشبكة مضبوط',
            'ok': bool(getattr(settings, 'MIKROTIK_CREDENTIAL_KEY', '')), 'blocking': True,
        },
        {
            'code': 'worker', 'label': 'عامل الإنترنت يعمل حالياً',
            'ok': worker_is_fresh(state, at=now), 'blocking': True,
        },
        {
            'code': 'failed_operations', 'label': 'لا توجد عمليات شبكة فاشلة معلقة',
            'ok': entitlement_failed == 0 and session_failed == 0, 'blocking': True,
            'detail': f'{entitlement_failed + session_failed} عملية فاشلة',
        },
        {
            'code': 'router_health', 'label': 'فحص MikroTik للقراءة فقط ناجح وحديث',
            'ok': last_check_ok, 'blocking': True,
            'detail': (
                'لم يجر فحص حديث' if not last_check_fresh
                else ('ناجح' if state and state.last_mikrotik_check_ok else 'فشل')
            ),
        },
    ]
    return {
        'ready': all(item['ok'] for item in checks if item['blocking']),
        'checks': checks,
        'state': state,
        'enabled': bool(getattr(settings, 'MIKROTIK_ENABLED', False)),
    }


def internet_readiness_report(*, at=None):
    """Authoritative read-only Internet readiness report used by CLI and staff UI."""
    now = at or timezone.now()
    findings = []

    def add(severity, code, message, **details):
        findings.append({'severity': severity, 'code': code, 'message': message, **details})

    packages = InternetPackage.objects.filter(is_active=True).select_related('partner', 'bandwidth_profile')
    if not packages.exists():
        add('WARN', 'no_active_packages', 'No active Internet packages are configured.')
    for package in packages:
        try:
            package.clean()
        except ValidationError as exc:
            add('FAIL', 'invalid_package', '; '.join(exc.messages), package_id=package.pk)

    if not InternetPartner.objects.filter(active=True, is_default=True).exists():
        add('WARN', 'no_default_partner', 'No default partner; partnerless sales create no liability.')
    if InternetBandwidthProfile.objects.filter(is_active=False, packages__is_active=True).exists():
        add('FAIL', 'inactive_package_profile', 'An active package uses an inactive bandwidth profile.')

    entitlement_failed = InternetNetworkOperation.objects.filter(
        status=InternetNetworkOperation.Status.FAILED,
    ).count()
    entitlement_pending = InternetNetworkOperation.objects.filter(status__in=(
        InternetNetworkOperation.Status.PENDING, InternetNetworkOperation.Status.PROCESSING,
    )).count()
    session_failed = InternetSessionNetworkOperation.objects.filter(
        status=InternetSessionNetworkOperation.Status.FAILED,
    ).count()
    session_pending = InternetSessionNetworkOperation.objects.filter(status__in=(
        InternetSessionNetworkOperation.Status.PENDING,
        InternetSessionNetworkOperation.Status.PROCESSING,
    )).count()

    if entitlement_failed:
        add('WARN', 'failed_network_operations', 'Failed entitlement network operations require review.', count=entitlement_failed)
    if entitlement_pending:
        add('WARN', 'pending_network_operations', 'Entitlement network operations remain pending.', count=entitlement_pending)
    if session_failed:
        add('WARN', 'failed_session_network_operations', 'Failed session network operations require review.', count=session_failed)
    if session_pending:
        add('WARN', 'pending_session_network_operations', 'Session network operations remain pending.', count=session_pending)

    for item in commercial_integrity_findings() + access_integrity_findings():
        details = dict(item)
        code = details.pop('code')
        add('FAIL', code, 'Internet business invariant violation.', **details)

    state = get_operations_state()
    if not worker_is_fresh(state, at=now):
        add('WARN', 'internet_worker_stale', 'Internet worker heartbeat is missing or stale.')

    if getattr(settings, 'MIKROTIK_ENABLED', False):
        if not mikrotik_static_configured():
            add('FAIL', 'mikrotik_incomplete', 'MikroTik is enabled but required safe configuration is incomplete.')
        if state and state.last_mikrotik_check_ok is False and mikrotik_check_is_fresh(state, at=now):
            add('FAIL', 'mikrotik_health_failed', 'The latest read-only MikroTik connectivity check failed.')
        elif not (state and state.last_mikrotik_check_ok is True and mikrotik_check_is_fresh(state, at=now)):
            add('WARN', 'mikrotik_health_stale', 'No recent successful read-only MikroTik connectivity check is recorded.')
    else:
        add('WARN', 'mikrotik_disabled', 'MikroTik disabled; Manual network enforcement remains the safe fallback.')

    status = 'FAIL' if any(f['severity'] == 'FAIL' for f in findings) else ('WARN' if findings else 'PASS')
    return {'status': status, 'findings': findings}
