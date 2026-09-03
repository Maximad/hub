"""Deployment checks for optional push notification configuration."""

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def check_push_notification_settings(app_configs, **kwargs):
    if not getattr(settings, 'PUSH_NOTIFICATIONS_ENABLED', False):
        return []

    errors = []
    provider = getattr(settings, 'PUSH_PROVIDER', '').strip().lower()
    if provider != 'webpush':
        errors.append(Error(
            'PUSH_PROVIDER must be "webpush" when push notifications are enabled.',
            id='core.E030',
        ))
        return errors

    required = {
        'VAPID_PUBLIC_KEY': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'VAPID_PRIVATE_KEY': getattr(settings, 'VAPID_PRIVATE_KEY', ''),
        'VAPID_SUBJECT': getattr(settings, 'VAPID_SUBJECT', ''),
    }
    missing = sorted(name for name, value in required.items() if not str(value).strip())
    if missing:
        errors.append(Error(
            f'Missing Web Push settings: {", ".join(missing)}.',
            hint='Keep the private key in the runtime environment, never in Git.',
            id='core.E031',
        ))

    subject = str(required['VAPID_SUBJECT']).strip()
    if subject and not subject.startswith(('mailto:', 'https://')):
        errors.append(Error(
            'VAPID_SUBJECT must be a mailto: or HTTPS contact URI.',
            id='core.E032',
        ))

    if required['VAPID_PUBLIC_KEY'] and required['VAPID_PUBLIC_KEY'] == required['VAPID_PRIVATE_KEY']:
        errors.append(Error(
            'VAPID public and private keys must not be identical.',
            id='core.E033',
        ))
    if getattr(settings, 'PUSH_HTTP_TIMEOUT_SECONDS', 0) <= 0:
        errors.append(Error(
            'PUSH_HTTP_TIMEOUT_SECONDS must be greater than zero.',
            id='core.E034',
        ))
    return errors
