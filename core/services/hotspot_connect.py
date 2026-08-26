"""Secure customer relay into a provisioned RouterOS HotSpot login."""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

from core.models import InternetNetworkOperation, InternetSession
from core.services.network_backends import MikroTikNetworkBackend
from core.services.network_operations import process_network_operation
from internet.models import InternetSessionNetworkOperation, InternetSessionNetworkState
from internet.session_network_backends import PROVISIONED, MikroTikSessionNetworkBackend
from internet.session_network_operations import process_session_network_operation


def hotspot_login_url():
    """Return the configured HTTPS HotSpot login servlet URL, or fail closed."""
    value = (getattr(settings, 'MIKROTIK_HOTSPOT_LOGIN_URL', '') or '').strip()
    if not value:
        raise ValidationError('رابط تسجيل الدخول إلى شبكة HotSpot غير مضبوط.')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValidationError('الاتصال التلقائي يتطلب رابط HotSpot آمن عبر HTTPS.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError('رابط HotSpot يجب ألا يحتوي بيانات دخول أو query أو fragment.')
    return value


def _hotspot_login_configured():
    if not getattr(settings, 'MIKROTIK_ENABLED', False):
        return False
    try:
        hotspot_login_url()
    except ValidationError:
        return False
    return True


def one_tap_connect_configured(entitlement):
    """Whether this entitlement can present the one-tap customer action."""
    return bool(entitlement is not None
                and entitlement.network_backend == 'mikrotik'
                and _hotspot_login_configured())


def one_tap_session_connect_configured(session):
    """Whether a package-less session can present the RouterOS relay action."""
    return bool(session is not None
                and session.entitlement_id is None
                and session.network_provider == InternetSession.NetworkProvider.MIKROTIK
                and session.status == InternetSession.Status.ACTIVE
                and _hotspot_login_configured())


def _try_pending_provision(entitlement):
    """Give a just-created pending provision job one immediate customer-triggered chance."""
    if entitlement.network_status == entitlement.NetworkStatus.PROVISIONED:
        return entitlement
    job = (
        InternetNetworkOperation.objects.filter(
            entitlement=entitlement,
            operation=InternetNetworkOperation.Operation.PROVISION,
            status=InternetNetworkOperation.Status.PENDING,
        )
        .order_by('-created_at')
        .first()
    )
    if job is not None:
        process_network_operation(job)
        entitlement.refresh_from_db()
    return entitlement


def _try_pending_session_provision(session):
    state = InternetSessionNetworkState.objects.filter(session=session).first()
    if session.network_status == PROVISIONED and state and state.network_activated_at:
        return session
    job = (
        InternetSessionNetworkOperation.objects.filter(
            session=session,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
            status__in=(
                InternetSessionNetworkOperation.Status.PENDING,
                InternetSessionNetworkOperation.Status.FAILED,
            ),
        )
        .order_by('created_at')
        .first()
    )
    if job is not None:
        process_session_network_operation(job)
        session.refresh_from_db()
    return session


def _payload(username, password, destination_url):
    login_url = hotspot_login_url()
    parsed = urlsplit(login_url)
    return {
        'login_url': login_url,
        'login_origin': f'{parsed.scheme}://{parsed.netloc}',
        'username': username,
        'password': password,
        'destination_url': destination_url,
    }


def build_hotspot_login_payload(entitlement, *, destination_url):
    """Return the short-lived browser POST payload for a provisioned entitlement.

    Credentials are decrypted only here, never placed in Hub URLs/log messages, and
    are expected to be rendered only into a no-store relay response.
    """
    if not one_tap_connect_configured(entitlement):
        raise ValidationError('الاتصال التلقائي بالشبكة غير متاح لهذه الباقة.')
    entitlement = _try_pending_provision(entitlement)
    if entitlement.network_status != entitlement.NetworkStatus.PROVISIONED:
        raise ValidationError('لم يكتمل تجهيز الشبكة بعد. جرّب مرة أخرى بعد قليل.')
    status = entitlement.effective_status()
    if status in {entitlement.Status.EXPIRED, entitlement.Status.CANCELLED}:
        raise ValidationError('انتهت صلاحية هذه الباقة.')
    if not entitlement.network_credential_encrypted:
        raise ValidationError('بيانات دخول الشبكة لم تُجهّز بعد.')

    backend = MikroTikNetworkBackend()
    username, password = backend.connection_credentials(entitlement)
    return _payload(username, password, destination_url)


def build_session_hotspot_login_payload(session, *, destination_url):
    """Return the no-store RouterOS relay payload for a metered InternetSession."""
    if not one_tap_session_connect_configured(session):
        raise ValidationError('الاتصال التلقائي بالشبكة غير متاح لهذه الجلسة.')
    session = _try_pending_session_provision(session)
    state = InternetSessionNetworkState.objects.filter(session=session).first()
    if (session.network_status != PROVISIONED
            or state is None
            or state.network_activated_at is None):
        raise ValidationError('لم يكتمل تجهيز الشبكة بعد. جرّب مرة أخرى بعد قليل.')
    if not state.network_credential_encrypted:
        raise ValidationError('بيانات دخول الشبكة لم تُجهّز بعد.')

    backend = MikroTikSessionNetworkBackend()
    username, password = backend.connection_credentials(session)
    return _payload(username, password, destination_url)
