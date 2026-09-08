from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import IntegrationRequestLog, IntegrationToken

TOKEN_VERSION = 'hubm1'
ALL_SCOPES = frozenset({
    'mcp.connect',
    'schema.read',
    'catalog.read',
    'catalog.write',
    'inventory.read',
    'recipes.read',
})


@dataclass(frozen=True)
class IntegrationCredential:
    token: IntegrationToken
    request_id: uuid.UUID


class IntegrationAuthError(Exception):
    def __init__(self, message: str, *, status: int = 401, code: str = 'unauthorized'):
        super().__init__(message)
        self.status = status
        self.code = code


def management_api_enabled() -> bool:
    return os.getenv('HUB_MANAGEMENT_API_ENABLED', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def management_api_require_https() -> bool:
    raw = os.getenv('HUB_MANAGEMENT_API_REQUIRE_HTTPS', 'true').strip().lower()
    return raw not in {'0', 'false', 'no', 'off'}


def confirmation_ttl_seconds() -> int:
    raw = os.getenv('HUB_MANAGEMENT_API_CONFIRMATION_TTL_SECONDS', '600').strip()
    try:
        value = int(raw)
    except ValueError:
        return 600
    return min(max(value, 60), 3600)


def generate_token_value() -> tuple[str, str, str]:
    """Return (prefix, secret_digest, complete_token)."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    digest = hashlib.sha256(secret.encode('utf-8')).hexdigest()
    return prefix, digest, f'{TOKEN_VERSION}.{prefix}.{secret}'


def validate_scopes(scopes) -> list[str]:
    normalized = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    unknown = [scope for scope in normalized if scope not in ALL_SCOPES]
    if unknown:
        raise ValueError(f'Unknown integration scopes: {", ".join(unknown)}')
    return normalized


def _parse_bearer(raw_header: str) -> tuple[str, str]:
    if not raw_header.startswith('Bearer '):
        raise IntegrationAuthError('Bearer authentication is required.')
    raw_token = raw_header[7:].strip()
    parts = raw_token.split('.')
    if len(parts) != 3 or parts[0] != TOKEN_VERSION:
        raise IntegrationAuthError('Invalid management integration credential.')
    _version, prefix, secret = parts
    if not prefix or not secret:
        raise IntegrationAuthError('Invalid management integration credential.')
    return prefix, secret


def authenticate_bearer_header(raw_header: str, required_scope: str | None = None) -> IntegrationToken:
    """Authenticate a Hub integration bearer credential.

    REST management endpoints and the MCP sidecar share this verifier so token
    expiry, revocation, digest comparison and scope checks cannot drift apart.
    Passing a scope performs authorization as part of the same operation.
    """
    if not management_api_enabled():
        raise IntegrationAuthError('Management API is disabled.', status=503, code='integration_disabled')
    if required_scope is not None and required_scope not in ALL_SCOPES:
        raise IntegrationAuthError('Server integration scope is invalid.', status=500, code='invalid_server_scope')

    prefix, secret = _parse_bearer(raw_header)
    try:
        token = IntegrationToken.objects.get(prefix=prefix, is_active=True)
    except IntegrationToken.DoesNotExist as exc:
        raise IntegrationAuthError('Invalid management integration credential.') from exc

    if token.expires_at and token.expires_at <= timezone.now():
        raise IntegrationAuthError('Management integration credential has expired.')

    candidate_digest = hashlib.sha256(secret.encode('utf-8')).hexdigest()
    if not hmac.compare_digest(candidate_digest, token.secret_digest):
        raise IntegrationAuthError('Invalid management integration credential.')

    if required_scope is not None and required_scope not in set(token.scopes or []):
        raise IntegrationAuthError(
            f'Missing required scope: {required_scope}',
            status=403,
            code='insufficient_scope',
        )

    IntegrationToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token


def authenticate_request(request, required_scope: str) -> IntegrationCredential:
    if management_api_require_https() and not request.is_secure():
        raise IntegrationAuthError('HTTPS is required.', status=400, code='https_required')

    token = authenticate_bearer_header(request.headers.get('Authorization', ''), required_scope)
    request_id = _request_id(request)
    return IntegrationCredential(token=token, request_id=request_id)


def integration_endpoint(scope: str, *, methods=('GET',)):
    """Authenticate a management API endpoint and append metadata-only audit."""
    allowed_methods = frozenset(method.upper() for method in methods)

    def decorator(view_func):
        @csrf_exempt
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            credential = None
            request_id = _request_id(request)
            response = None
            try:
                if request.method.upper() not in allowed_methods:
                    response = JsonResponse(
                        {'ok': False, 'error': {'code': 'method_not_allowed', 'message': 'Method not allowed.'}, 'request_id': str(request_id)},
                        status=405,
                    )
                    response['Allow'] = ', '.join(sorted(allowed_methods))
                    return response
                credential = authenticate_request(request, scope)
                request.integration_token = credential.token
                request.integration_request_id = credential.request_id
                request_id = credential.request_id
                response = view_func(request, *args, **kwargs)
                if isinstance(response, JsonResponse):
                    response['X-Hub-Request-ID'] = str(request_id)
                return response
            except IntegrationAuthError as exc:
                response = JsonResponse(
                    {'ok': False, 'error': {'code': exc.code, 'message': str(exc)}, 'request_id': str(request_id)},
                    status=exc.status,
                )
                response['X-Hub-Request-ID'] = str(request_id)
                return response
            finally:
                # Persist API audit metadata only after successful authentication.
                # This avoids turning unauthenticated probes into database-write
                # amplification while reverse-proxy logs still capture rejects.
                if response is not None and credential is not None:
                    _record_request(
                        token=credential.token,
                        request=request,
                        request_id=request_id,
                        scope=scope,
                        status_code=response.status_code,
                    )

        return wrapper

    return decorator


def _request_id(request) -> uuid.UUID:
    raw = (request.headers.get('X-Request-ID') or '').strip()
    if raw:
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    existing = getattr(request, '_hub_integration_request_id', None)
    if existing:
        return existing
    value = uuid.uuid4()
    request._hub_integration_request_id = value
    return value


def _record_request(*, token, request, request_id, scope, status_code):
    remote_addr = request.META.get('REMOTE_ADDR') or None
    try:
        IntegrationRequestLog.objects.create(
            token=token,
            request_id=request_id,
            method=request.method[:12],
            path=request.path[:255],
            scope=scope[:80],
            status_code=status_code,
            remote_addr=remote_addr,
        )
    except Exception:
        # API availability must not depend on the secondary request-audit write.
        pass
