from __future__ import annotations

import os
import uuid

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from asgiref.sync import sync_to_async
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from audit.integration_auth import (
    IntegrationAuthError,
    authenticate_bearer_header,
    management_api_enabled,
    management_api_require_https,
)
from audit.models import IntegrationRequestLog
from mcp.server.transport_security import TransportSecuritySettings

from .server import mcp


def _enabled(name: str, default: str = '') -> bool:
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        'HUB_MCP_ALLOWED_ORIGINS',
        'https://chatgpt.com,https://chat.openai.com',
    )
    return [value.strip().rstrip('/') for value in raw.split(',') if value.strip()]


def _headers(scope) -> dict[str, str]:
    return {
        key.decode('latin-1').lower(): value.decode('latin-1')
        for key, value in scope.get('headers', [])
    }


def _request_id(headers: dict[str, str]) -> uuid.UUID:
    raw = (headers.get('x-request-id') or '').strip()
    if raw:
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    return uuid.uuid4()


async def _record_request(*, token, scope, request_id, status_code):
    client = scope.get('client') or (None, None)
    remote_addr = client[0] if client else None
    headers = _headers(scope)
    tool_name = (headers.get('mcp-name') or '').strip()
    audit_scope = f'mcp:{tool_name}' if tool_name else 'mcp'
    try:
        await sync_to_async(IntegrationRequestLog.objects.create, thread_sensitive=True)(
            token=token,
            request_id=request_id,
            method=(scope.get('method') or 'MCP')[:12],
            path=(scope.get('path') or '/mcp')[:255],
            scope=audit_scope[:80],
            status_code=status_code,
            remote_addr=remote_addr,
        )
    except Exception:
        # The operational tool response must not depend on secondary audit I/O.
        pass


class HubMCPAuthMiddleware:
    """Fail-closed bearer gate for the MCP transport.

    A credential must explicitly carry ``mcp.connect`` before it can discover
    the MCP tool surface. Tool handlers then repeat authorization with their
    own business-data scope (catalog.read, inventory.read, etc.).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        request_id = _request_id(headers)

        if not _enabled('HUB_MCP_ENABLED'):
            response = JSONResponse(
                {
                    'ok': False,
                    'error': {'code': 'mcp_disabled', 'message': 'Hub MCP bridge is disabled.'},
                    'request_id': str(request_id),
                },
                status_code=503,
                headers={'X-Hub-Request-ID': str(request_id)},
            )
            await response(scope, receive, send)
            return

        if not management_api_enabled():
            response = JSONResponse(
                {
                    'ok': False,
                    'error': {'code': 'integration_disabled', 'message': 'Management API is disabled.'},
                    'request_id': str(request_id),
                },
                status_code=503,
                headers={'X-Hub-Request-ID': str(request_id)},
            )
            await response(scope, receive, send)
            return

        forwarded_proto = (headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
        is_https = scope.get('scheme') == 'https' or forwarded_proto == 'https'
        if management_api_require_https() and not is_https:
            response = JSONResponse(
                {
                    'ok': False,
                    'error': {'code': 'https_required', 'message': 'HTTPS is required.'},
                    'request_id': str(request_id),
                },
                status_code=400,
                headers={'X-Hub-Request-ID': str(request_id)},
            )
            await response(scope, receive, send)
            return

        token = None
        try:
            token = await sync_to_async(authenticate_bearer_header, thread_sensitive=True)(
                headers.get('authorization', ''),
                'mcp.connect',
            )
        except IntegrationAuthError as exc:
            response = JSONResponse(
                {
                    'ok': False,
                    'error': {'code': exc.code, 'message': str(exc)},
                    'request_id': str(request_id),
                },
                status_code=exc.status,
                headers={
                    'X-Hub-Request-ID': str(request_id),
                    'WWW-Authenticate': 'Bearer',
                },
            )
            await response(scope, receive, send)
            return

        status_code = 500

        async def send_with_request_id(message):
            nonlocal status_code
            if message.get('type') == 'http.response.start':
                status_code = int(message.get('status', 500))
                response_headers = list(message.get('headers', []))
                response_headers.append((b'x-hub-request-id', str(request_id).encode('ascii')))
                message['headers'] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            if token is not None:
                await _record_request(
                    token=token,
                    scope=scope,
                    request_id=request_id,
                    status_code=status_code,
                )


public_host = os.getenv('HUB_MCP_PUBLIC_HOST', 'hubsweida.jwtalenthouse.com').strip()
origins = _allowed_origins()
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[public_host, f'{public_host}:*'],
    allowed_origins=origins,
)

mcp_http_app = mcp.streamable_http_app(
    streamable_http_path='/mcp',
    json_response=True,
    stateless_http=True,
    transport_security=transport_security,
)

authenticated_app = HubMCPAuthMiddleware(mcp_http_app)
app = CORSMiddleware(
    authenticated_app,
    allow_origins=origins,
    allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
    allow_headers=[
        'Authorization',
        'Content-Type',
        'Last-Event-ID',
        'Mcp-Method',
        'Mcp-Name',
        'Mcp-Protocol-Version',
        'Mcp-Session-Id',
        'X-Request-ID',
    ],
    expose_headers=['Mcp-Session-Id', 'X-Hub-Request-ID'],
)
