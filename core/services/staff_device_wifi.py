"""Browser-scoped RouterOS logins for unlimited staff Internet grants.

Keep the existing grant's HotSpot user untouched for grandfathered connections.
Each authorized browser receives a separate *session-owned* HotSpot user, using
the existing durable session-network outbox and the grant's existing profile.
No RouterOS profile/server, speed, firewall or walled-garden writes are made.

A browser cookie is not a verified physical-device identity. This bridge is
limited to unlimited internal grants and does not claim a MAC-based quota.
"""
import hashlib
import hmac
import re
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, InternetEntitlement, InternetSession
from core.services.internet_access import start_usage_session
from core.services.internet_internal_access import INTERNAL_ORIGINS
from internet.models import InternalStaffInternetGrant, InternetSessionNetworkOperation
from internet.session_network_backends import NOT_PROVISIONED
from internet.session_network_operations import enqueue_session_network_operation


COOKIE_NAME = 'hub_staff_wifi_device'
COOKIE_MAX_AGE = 60 * 60 * 24 * 90
SESSION_MARKER = 'staff_wifi_device_v1'
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


def supports_independent_staff_login(entitlement):
    """Only network-managed, unlimited internal grants use this initial bridge."""
    return bool(
        entitlement
        and settings.MIKROTIK_ENABLED
        and entitlement.network_backend == 'mikrotik'
        and entitlement.origin_type in INTERNAL_ORIGINS
        and entitlement.access_mode == 'unlimited'
        and int(entitlement.max_concurrent_devices or 0) > 1
    )


def _cookie_digest(*, entitlement_id, user_id, token):
    message = f'staff-wifi:{entitlement_id}:{user_id}:{token}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


@transaction.atomic
def ensure_staff_device_session(*, request, entitlement):
    """Reserve/reuse exactly one provisionable HotSpot identity per browser.

    All requests are owner-checked and serialized by the entitlement row. New
    sessions retain the grant as lifecycle authority, but they use the existing
    session-owned MikroTik identity instead of sharing the entitlement username.
    """
    user = request.user
    if not user.is_authenticated or not user.is_active:
        raise ValidationError('تسجيل دخول الموظف مطلوب.')
    entitlement = InternetEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    if not supports_independent_staff_login(entitlement):
        raise ValidationError('الاتصال المستقل غير متاح لهذه المنحة.')
    if not InternalStaffInternetGrant.objects.filter(
        entitlement_id=entitlement.pk, user_id=user.pk,
    ).exists():
        raise ValidationError('لا تملك صلاحية استخدام منحة الإنترنت هذه.')
    now = timezone.now()
    if entitlement.effective_status(now) != entitlement.Status.ACTIVE:
        raise ValidationError('منحة الإنترنت غير فعالة.')

    token = request.COOKIES.get(COOKIE_NAME, '')
    if not TOKEN_PATTERN.fullmatch(token):
        token = secrets.token_urlsafe(32)
    digest = _cookie_digest(
        entitlement_id=entitlement.pk, user_id=user.pk, token=token,
    )
    existing = InternetSession.objects.filter(
        entitlement_id=entitlement.pk,
        started_by_id=user.pk,
        access_code=digest,
        notes=SESSION_MARKER,
        status=InternetSession.Status.ACTIVE,
    ).order_by('-pk').first()
    if existing and (existing.authorized_until is None or existing.authorized_until > now):
        return existing, token

    # Count registered *active browser sessions*, never other users' sessions.
    # Legacy grant credentials are preserved until existing sessions naturally
    # terminate; they are not silently disabled or rotated by this change.
    active_count = InternetSession.objects.filter(
        entitlement_id=entitlement.pk,
        notes=SESSION_MARKER,
        status=InternetSession.Status.ACTIVE,
        authorized_until__gt=now,
    ).count()
    cap = min(int(entitlement.max_concurrent_devices), int(entitlement.max_registered_devices))
    if active_count >= cap:
        raise ValidationError('بلغت منحة الإنترنت عدد الأجهزة المسموح به. راجع إدارة الإنترنت.')

    session = start_usage_session(entitlement, actor=user, at=now)
    session.access_code = digest
    session.notes = SESSION_MARKER
    session.network_status = NOT_PROVISIONED
    session.save(update_fields=['access_code', 'notes', 'network_status', 'updated_at'])
    enqueue_session_network_operation(
        session,
        InternetSessionNetworkOperation.Operation.PROVISION,
        reason='staff browser-scoped Internet connection',
        idempotency_key=f'staff-browser-session:{session.pk}:provision',
        process_after_commit=False,
    )
    ActivityLog.objects.create(
        actor=user, action='internet.staff_device_session_created',
        details={'entitlement_id': entitlement.pk, 'session_id': session.pk},
    )
    return session, token


def set_staff_device_cookie(response, token, *, secure=True):
    response.set_cookie(
        COOKIE_NAME, token, max_age=COOKIE_MAX_AGE,
        httponly=True, secure=secure, samesite='Lax', path='/',
    )
    return response
