import logging
import os

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class LoginDeliveryUnavailable(Exception):
    pass


LOC_MEM_OUTBOX = []


def _setting(name, default=''):
    value = getattr(settings, name, None)
    if value is None:
        value = os.getenv(name, default)
    return value


def _send_locmem(phone, code, challenge_uuid):
    LOC_MEM_OUTBOX.append({
        'phone': phone,
        'code': code,
        'challenge_uuid': str(challenge_uuid),
    })


def _send_webhook(phone, code, challenge_uuid):
    url = str(_setting('MEMBER_LOGIN_WEBHOOK_URL', '')).strip()
    if not url:
        raise LoginDeliveryUnavailable('member login webhook is not configured')
    headers = {'Content-Type': 'application/json'}
    token = str(_setting('MEMBER_LOGIN_WEBHOOK_TOKEN', '')).strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        response = requests.post(
            url,
            json={
                'phone': phone,
                'code': code,
                'purpose': 'member_login',
                'challenge_id': str(challenge_uuid),
                'expires_in_seconds': int(_setting('MEMBER_LOGIN_CODE_AGE', 600)),
            },
            headers=headers,
            timeout=float(_setting('MEMBER_LOGIN_WEBHOOK_TIMEOUT_SECONDS', 5)),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LoginDeliveryUnavailable('member login delivery failed') from exc


def send_login_code(phone, code, challenge_uuid):
    backend = str(_setting('MEMBER_LOGIN_DELIVERY_BACKEND', 'disabled')).strip().lower()
    if backend == 'locmem':
        _send_locmem(phone, code, challenge_uuid)
        return
    if backend == 'webhook':
        _send_webhook(phone, code, challenge_uuid)
        return
    if backend == 'disabled':
        raise LoginDeliveryUnavailable('member login delivery is disabled')
    logger.error('Unknown member login delivery backend: %s', backend)
    raise LoginDeliveryUnavailable('member login delivery backend is invalid')
