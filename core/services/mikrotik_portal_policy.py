"""Narrow, idempotent RouterOS policy owned by the Hub captive portal."""
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

from core.models import InternetBandwidthProfile
from core.services.mikrotik import RouterOSClient
from internet.models import GuestWifiPolicy


MANAGED_COMMENT = 'hub-managed:portal-policy'


def _router_client():
    return RouterOSClient(
        base_url=getattr(settings, 'MIKROTIK_BASE_URL', ''),
        username=getattr(settings, 'MIKROTIK_USERNAME', ''),
        password=getattr(settings, 'MIKROTIK_PASSWORD', ''),
        verify_tls=getattr(settings, 'MIKROTIK_VERIFY_TLS', True),
        ca_file=getattr(settings, 'MIKROTIK_CA_FILE', ''),
        connect_timeout=getattr(settings, 'MIKROTIK_CONNECT_TIMEOUT', 5),
        read_timeout=getattr(settings, 'MIKROTIK_READ_TIMEOUT', 10),
    )


def _portal_host():
    explicit = (getattr(settings, 'MIKROTIK_PORTAL_HOST', '') or '').strip().lower()
    if explicit:
        host = explicit
    else:
        public_url = (getattr(settings, 'PUBLIC_BASE_URL', '') or '').strip()
        host = (urlsplit(public_url).hostname or '').lower() if public_url else ''
    if not host or not re.fullmatch(r'[a-z0-9.-]+', host):
        raise ValidationError('اسم نطاق بوابة هَبّ غير مضبوط لإدارة HotSpot.')
    return host


def _profile_spec(profile, *, label):
    if not profile or not profile.is_active:
        raise ValidationError(f'ملف سرعة {label} غير مضبوط أو غير فعّال.')
    if not profile.router_profile_name:
        raise ValidationError(f'ملف سرعة {label} غير مربوط بملف RouterOS.')
    if not profile.upload_limit_kbps or not profile.download_limit_kbps:
        raise ValidationError(f'سرعة الرفع والتنزيل لملف {label} غير مكتملة.')
    rate_limit = f'{int(profile.upload_limit_kbps)}k/{int(profile.download_limit_kbps)}k'
    return {
        'code': profile.code,
        'name': profile.router_profile_name,
        'rate-limit': rate_limit,
        'rate_limit': rate_limit,
        'shared-users': '1',
        'comment': MANAGED_COMMENT,
    }


def desired_portal_policy():
    policy = GuestWifiPolicy.objects.select_related('bandwidth_profile').filter(key='default').first()
    if not policy or not policy.bandwidth_profile_id:
        raise ValidationError('اختر ملف سرعة الإنترنت الأساسي أولاً.')
    fast_code = (getattr(settings, 'MIKROTIK_CUSTOMER_PROFILE_CODE', 'fast') or 'fast').strip()
    fast = InternetBandwidthProfile.objects.filter(code=fast_code).first()
    profiles = [
        _profile_spec(policy.bandwidth_profile, label='الإنترنت الأساسي'),
        _profile_spec(fast, label='الإنترنت السريع'),
    ]
    if profiles[0]['name'] == profiles[1]['name']:
        raise ValidationError('يجب أن يكون للإنترنت الأساسي والسريع ملفا RouterOS مختلفان.')
    server = (getattr(settings, 'MIKROTIK_HOTSPOT_SERVER', '') or '').strip()
    if not server:
        raise ValidationError('اسم خادم HotSpot الخاص بهَبّ غير مضبوط.')
    return {'server': server, 'portal_host': _portal_host(), 'profiles': profiles}


def _rate_value(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*', value or '')
    if not match:
        return None
    multiplier = {'': 1, 'k': 1000, 'm': 1000 ** 2, 'g': 1000 ** 3}[match.group(2).lower()]
    return int(float(match.group(1)) * multiplier)


def _same_rate(left, right):
    left_parts = (left or '').split('/')
    right_parts = (right or '').split('/')
    return len(left_parts) == len(right_parts) == 2 and all(
        _rate_value(a) == _rate_value(b) for a, b in zip(left_parts, right_parts)
    )


def inspect_portal_policy(*, client=None):
    """Read only the exact Hub server, two profiles, and Hub portal allow rule."""
    client = client or _router_client()
    desired = desired_portal_policy()
    checks = []
    server = client.find_hotspot_server(desired['server'])
    checks.append({
        'code': 'server',
        'label': 'خادم HotSpot الخاص بهَبّ',
        'ok': bool(server and server.get('disabled') != 'true' and server.get('invalid') != 'true'),
        'detail': desired['server'],
    })
    for spec in desired['profiles']:
        remote = client.find_profile(spec['name'])
        ok = bool(
            remote
            and remote.get('disabled', 'false') != 'true'
            and str(remote.get('shared-users', '1')) == '1'
            and _same_rate(remote.get('rate-limit', ''), spec['rate-limit'])
        )
        checks.append({
            'code': f"profile:{spec['code']}",
            'label': f"ملف السرعة {spec['name']}",
            'ok': ok,
            'detail': spec['rate-limit'],
        })
    garden = client.find_walled_garden(
        server=desired['server'],
        dst_host=desired['portal_host'],
    )
    checks.append({
        'code': 'portal',
        'label': 'وصول البوابة قبل تسجيل الدخول',
        'ok': bool(
            garden
            and garden.get('disabled', 'false') != 'true'
            and garden.get('action', 'allow') == 'allow'
        ),
        'detail': desired['portal_host'],
    })
    return {
        'ready': all(check['ok'] for check in checks),
        'checks': checks,
        'desired': desired,
        'changes': [],
    }


def sync_portal_policy(*, client=None):
    """Create or repair only the explicitly configured Hub-owned policy resources."""
    client = client or _router_client()
    desired = desired_portal_policy()
    server = client.find_hotspot_server(desired['server'])
    if not server or server.get('invalid') == 'true':
        raise ValidationError('خادم HotSpot الخاص بهَبّ غير موجود أو غير صالح؛ لم يُغيّر النظام شيئاً.')

    changes = []
    for spec in desired['profiles']:
        remote = client.find_profile(spec['name'])
        values = {
            'name': spec['name'],
            'rate-limit': spec['rate-limit'],
            'shared-users': '1',
            'comment': MANAGED_COMMENT,
        }
        if remote is None:
            client.create_profile(values)
            changes.append(f"created-profile:{spec['name']}")
        else:
            update = {}
            if not _same_rate(remote.get('rate-limit', ''), spec['rate-limit']):
                update['rate-limit'] = spec['rate-limit']
            if str(remote.get('shared-users', '1')) != '1':
                update['shared-users'] = '1'
            if remote.get('disabled') == 'true':
                update['disabled'] = 'false'
            if remote.get('comment') != MANAGED_COMMENT:
                update['comment'] = MANAGED_COMMENT
            if update:
                client.update_profile(remote['.id'], update)
                changes.append(f"updated-profile:{spec['name']}")

    garden = client.find_walled_garden(
        server=desired['server'],
        dst_host=desired['portal_host'],
    )
    garden_values = {
        'server': desired['server'],
        'dst-host': desired['portal_host'],
        'action': 'allow',
        'comment': MANAGED_COMMENT,
    }
    if garden is None:
        client.create_walled_garden(garden_values)
        changes.append('created-portal-access')
    else:
        update = {}
        if garden.get('action', 'allow') != 'allow':
            update['action'] = 'allow'
        if garden.get('disabled') == 'true':
            update['disabled'] = 'false'
        if garden.get('comment') != MANAGED_COMMENT:
            update['comment'] = MANAGED_COMMENT
        if update:
            client.update_walled_garden(garden['.id'], update)
            changes.append('updated-portal-access')

    report = inspect_portal_policy(client=client)
    report['changes'] = changes
    return report
