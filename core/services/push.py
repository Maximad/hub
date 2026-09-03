"""Provider boundary for staff push delivery.

This module performs no recipient selection and is not called from request
transactions. A later durable worker owns delivery, retries, and deactivation.
"""

import json
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from pywebpush import webpush


@dataclass(frozen=True)
class PushPayload:
    title: str
    body: str = ''
    link: str = '/staff/'
    tag: str = ''

    def __post_init__(self):
        if not self.title.strip():
            raise ValueError('Push title is required.')
        if not self.link.startswith('/staff/'):
            raise ValueError('Push links must remain inside the authenticated staff area.')

    def as_dict(self):
        payload = {
            'title': self.title,
            'body': self.body,
            'url': self.link,
        }
        if self.tag:
            payload['tag'] = self.tag
        return payload


@dataclass(frozen=True)
class PushSendResult:
    accepted: bool
    status_code: int | None = None
    provider_message_id: str = ''
    error_code: str = ''


class PushTransportError(Exception):
    def __init__(self, error_code, *, permanent=False, status_code=None):
        super().__init__(error_code)
        self.error_code = error_code
        self.permanent = permanent
        self.status_code = status_code


class DisabledPushTransport:
    provider = 'disabled'

    def send(self, subscription, payload):
        return PushSendResult(accepted=False, error_code='push_disabled')


class WebPushTransport:
    provider = 'webpush'

    def __init__(self, *, private_key, subject, timeout=10):
        if not private_key or not subject:
            raise ImproperlyConfigured('Web Push transport requires private VAPID settings.')
        self.private_key = private_key
        self.subject = subject
        self.timeout = timeout

    def send(self, subscription, payload):
        try:
            response = webpush(
                subscription_info={
                    'endpoint': subscription.endpoint,
                    'keys': {
                        'p256dh': subscription.p256dh,
                        'auth': subscription.auth_secret,
                    },
                },
                data=json.dumps(payload.as_dict(), ensure_ascii=False, separators=(',', ':')),
                vapid_private_key=self.private_key,
                vapid_claims={'sub': self.subject},
                ttl=60,
                timeout=self.timeout,
            )
        except Exception as exc:
            response = getattr(exc, 'response', None)
            status_code = getattr(response, 'status_code', None)
            permanent = status_code in {404, 410}
            error_code = 'subscription_gone' if permanent else (
                f'provider_http_{status_code}' if status_code else 'provider_error'
            )
            raise PushTransportError(
                error_code,
                permanent=permanent,
                status_code=status_code,
            ) from exc

        status_code = getattr(response, 'status_code', None)
        return PushSendResult(
            accepted=status_code is None or 200 <= status_code < 300,
            status_code=status_code,
        )


def get_push_transport():
    if not getattr(settings, 'PUSH_NOTIFICATIONS_ENABLED', False):
        return DisabledPushTransport()
    provider = getattr(settings, 'PUSH_PROVIDER', '').strip().lower()
    if provider == 'webpush':
        return WebPushTransport(
            private_key=getattr(settings, 'VAPID_PRIVATE_KEY', ''),
            subject=getattr(settings, 'VAPID_SUBJECT', ''),
            timeout=getattr(settings, 'PUSH_HTTP_TIMEOUT_SECONDS', 10),
        )
    raise ImproperlyConfigured(f'Unsupported push provider: {provider or "empty"}.')
