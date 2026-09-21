"""Read-only validation of the fixed RouterOS resources used by Hub."""
from django.conf import settings
from django.core.exceptions import ValidationError

from core.models import InternetBandwidthProfile
from core.services.mikrotik import RouterOSClient
from internet.models import GuestWifiPolicy


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


def _profile_mapping(profile, *, label):
    if not profile or not profile.is_active:
        raise ValidationError(f'ملف {label} غير مضبوط أو غير فعّال.')
    router_name = (profile.router_profile_name or '').strip()
    if not router_name:
        raise ValidationError(f'ملف {label} غير مربوط بملف RouterOS موجود.')
    return {
        'code': profile.code,
        'label': label,
        'name': router_name,
    }


def configured_router_mappings():
    """Return Django's fixed references to existing RouterOS resources.

    Local speed values are deliberately excluded: they are descriptive historical
    metadata and are never treated as desired router configuration.
    """
    policy = GuestWifiPolicy.objects.select_related('bandwidth_profile').filter(key='default').first()
    if not policy or not policy.bandwidth_profile_id:
        raise ValidationError('اختر ملف الاتصال الأساسي أولاً.')

    fast_code = (getattr(settings, 'MIKROTIK_CUSTOMER_PROFILE_CODE', 'fast') or 'fast').strip()
    fast = InternetBandwidthProfile.objects.filter(code=fast_code).first()
    profiles = [
        _profile_mapping(policy.bandwidth_profile, label='الإنترنت الأساسي'),
        _profile_mapping(fast, label='الإنترنت السريع'),
    ]
    if profiles[0]['name'] == profiles[1]['name']:
        raise ValidationError('يجب أن يرتبط الإنترنت الأساسي والسريع بملفي RouterOS مختلفين.')

    server = (getattr(settings, 'MIKROTIK_HOTSPOT_SERVER', '') or '').strip()
    if not server:
        raise ValidationError('اسم خادم HotSpot الخاص بهَبّ غير مضبوط.')

    return {
        'server': server,
        'profiles': profiles,
    }


def inspect_router_mappings(*, client=None):
    """Read only the configured HotSpot server and mapped user profiles.

    Usability depends on the referenced resources existing and being enabled.
    Rate limits, comments, shared-user values and walled-garden rules are provider
    configuration and are intentionally not compared with Django values.
    """
    client = client or _router_client()
    mappings = configured_router_mappings()
    checks = []

    server = client.find_hotspot_server(mappings['server'])
    checks.append({
        'code': 'server',
        'label': 'خادم HotSpot الخاص بهَبّ',
        'ok': bool(server and server.get('disabled') != 'true' and server.get('invalid') != 'true'),
        'detail': mappings['server'],
    })

    for spec in mappings['profiles']:
        remote = client.find_profile(spec['name'])
        checks.append({
            'code': f"profile:{spec['code']}",
            'label': f"{spec['label']}: {spec['name']}",
            'ok': bool(remote and remote.get('disabled', 'false') != 'true'),
            'detail': spec['name'],
        })

    return {
        'ready': all(check['ok'] for check in checks),
        'checks': checks,
        'mappings': mappings,
    }
