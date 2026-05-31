from .settings_helpers import get_system_settings
from accounts.permissions import get_staff_capabilities


def system_settings(request):
    try:
        settings = get_system_settings()
    except Exception:
        settings = None
    return {'system_settings': settings, 'appearance_settings': settings, 'staff_caps': get_staff_capabilities(request.user)}
