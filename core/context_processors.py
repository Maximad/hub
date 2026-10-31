from .settings_helpers import get_system_settings
from .models import SystemSetting
from accounts.permissions import get_staff_capabilities


def system_settings(request):
    try:
        settings = get_system_settings()
    except Exception:
        settings = None
    numbers = settings.safe_theme_numbers if settings else SystemSetting().safe_theme_numbers
    return {
        'system_settings': settings,
        'appearance_settings': settings,
        'hub_theme_numbers': numbers,
        'hub_icons_enabled': bool(settings is None or settings.show_interface_icons),
        'staff_caps': get_staff_capabilities(request.user),
        'view_as_active': bool(getattr(request, 'view_as_target', None)),
        'view_as_actor': getattr(request, 'view_as_actor', None),
        'view_as_target': getattr(request, 'view_as_target', None),
    }


def public_attendance_navigation(request):
    from django.conf import settings
    from reservations.attendance_tracking import OWNER_SESSION_KEY
    session = getattr(request, 'session', {})
    token = session.get(OWNER_SESSION_KEY) if settings.PUBLIC_HOMEPAGE_ENABLED else None
    return {'attendance_tracking_available': isinstance(token, str) and bool(token)}
