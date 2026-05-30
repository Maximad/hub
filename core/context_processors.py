from .settings_helpers import get_system_settings


def system_settings(request):
    try:
        settings = get_system_settings()
    except Exception:
        settings = None
    return {'system_settings': settings}
