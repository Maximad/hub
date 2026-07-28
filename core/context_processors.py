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
    }
