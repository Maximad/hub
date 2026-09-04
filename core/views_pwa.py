"""PWA assets and authenticated browser push subscription registration."""

import hashlib
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_http_methods

from accounts.permissions import user_has_capability
from core.models import NotificationPreference, PushSubscription


MAX_REQUEST_BYTES = 8192
MAX_ENDPOINT_LENGTH = 2048
MAX_P256DH_LENGTH = 512
MAX_AUTH_SECRET_LENGTH = 256


def _deny(user):
    return not (user.is_authenticated and user_has_capability(user, 'staff_home'))


def _registration_enabled():
    return bool(
        getattr(settings, 'PUSH_NOTIFICATIONS_ENABLED', False)
        and getattr(settings, 'PUSH_PROVIDER', '').strip().lower() == 'webpush'
        and getattr(settings, 'VAPID_PUBLIC_KEY', '').strip()
    )


def _private_json(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'private, no-store'
    patch_vary_headers(response, ('Cookie',))
    return response


def _parse_json(request):
    try:
        content_length = int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        content_length = MAX_REQUEST_BYTES + 1
    if content_length > MAX_REQUEST_BYTES or len(request.body) > MAX_REQUEST_BYTES:
        return None, _private_json({'ok': False, 'error': 'request_too_large'}, status=413)
    if request.content_type != 'application/json':
        return None, _private_json({'ok': False, 'error': 'json_required'}, status=415)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _private_json({'ok': False, 'error': 'invalid_json'}, status=400)
    if not isinstance(payload, dict):
        return None, _private_json({'ok': False, 'error': 'invalid_json'}, status=400)
    return payload, None


def _endpoint_allowed(endpoint):
    if not isinstance(endpoint, str):
        return False
    endpoint = endpoint.strip()
    if not endpoint or len(endpoint) > MAX_ENDPOINT_LENGTH:
        return False
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return False
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip('.')
    for allowed in getattr(settings, 'PUSH_ENDPOINT_ALLOWED_HOSTS', ()):
        allowed = str(allowed).strip().lower().rstrip('.')
        if not allowed:
            continue
        if allowed.startswith('.'):
            suffix = allowed[1:]
            if host == suffix or host.endswith('.' + suffix):
                return True
        elif host == allowed:
            return True
    return False


def _subscription_values(payload):
    endpoint = payload.get('endpoint')
    keys = payload.get('keys')
    if not _endpoint_allowed(endpoint) or not isinstance(keys, dict):
        return None
    p256dh = keys.get('p256dh')
    auth_secret = keys.get('auth')
    if not isinstance(p256dh, str) or not isinstance(auth_secret, str):
        return None
    p256dh = p256dh.strip()
    auth_secret = auth_secret.strip()
    if not p256dh or len(p256dh) > MAX_P256DH_LENGTH:
        return None
    if not auth_secret or len(auth_secret) > MAX_AUTH_SECRET_LENGTH:
        return None
    device_label = payload.get('device_label', '')
    if not isinstance(device_label, str):
        device_label = ''
    return endpoint.strip(), p256dh, auth_secret, device_label.strip()[:80]


@require_GET
def staff_web_app_manifest(request):
    payload = {
        'id': '/staff/',
        'name': 'Hub Sweida — لوحة العمليات',
        'short_name': 'Hub Sweida',
        'description': 'لوحة عمليات هَب السويداء',
        'lang': 'ar',
        'dir': 'rtl',
        'start_url': '/staff/',
        'scope': '/staff/',
        'display': 'standalone',
        'background_color': '#f4f0e9',
        'theme_color': '#176b5a',
        'icons': [
            {'src': static('img/pwa-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': static('img/pwa-512.png'), 'sizes': '512x512', 'type': 'image/png'},
        ],
    }
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        content_type='application/manifest+json; charset=utf-8',
    )
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@require_GET
def service_worker(request):
    response = HttpResponse(
        render_to_string('service-worker.js'),
        content_type='application/javascript; charset=utf-8',
    )
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Service-Worker-Allowed'] = '/'
    return response


@login_required
@require_GET
def staff_push_config(request):
    if _deny(request.user):
        raise Http404()
    preference, _ = NotificationPreference.objects.get_or_create(user=request.user)
    enabled = _registration_enabled()
    return _private_json({
        'enabled': enabled,
        'public_key': getattr(settings, 'VAPID_PUBLIC_KEY', '').strip() if enabled else '',
        'preference_enabled': preference.enable_browser_notifications,
    })


@login_required
@require_http_methods(['POST', 'DELETE'])
def staff_push_subscription(request):
    if _deny(request.user):
        raise Http404()

    payload, error = _parse_json(request)
    if error:
        return error
    values = _subscription_values(payload)
    if values is None:
        return _private_json({'ok': False, 'error': 'invalid_subscription'}, status=400)
    endpoint, p256dh, auth_secret, device_label = values
    endpoint_hash = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()

    if request.method == 'DELETE':
        now = timezone.now()
        updated = PushSubscription.objects.filter(
            user=request.user,
            endpoint_hash=endpoint_hash,
            is_active=True,
        ).update(is_active=False, revoked_at=now, updated_at=now)
        return _private_json({'ok': True, 'revoked': bool(updated)})

    if not _registration_enabled():
        return _private_json({'ok': False, 'error': 'push_disabled'}, status=503)

    user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]
    with transaction.atomic():
        subscription, created = PushSubscription.objects.update_or_create(
            endpoint_hash=endpoint_hash,
            defaults={
                'user': request.user,
                'provider': PushSubscription.Provider.WEBPUSH,
                'endpoint': endpoint,
                'p256dh': p256dh,
                'auth_secret': auth_secret,
                'device_label': device_label,
                'user_agent': user_agent,
                'permission_state': PushSubscription.PermissionState.GRANTED,
                'is_active': True,
                'last_seen_at': timezone.now(),
                'revoked_at': None,
                'failure_count': 0,
            },
        )
        NotificationPreference.objects.update_or_create(
            user=request.user,
            defaults={'enable_browser_notifications': True},
        )

    return _private_json({
        'ok': True,
        'subscription_id': subscription.pk,
        'active': subscription.is_active,
        'created': created,
    }, status=201 if created else 200)
