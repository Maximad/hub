import os

from django.conf import settings


def int_setting(name, default, *, minimum=None, maximum=None):
    value = getattr(settings, name, None)
    if value is None:
        value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    if minimum is not None:
        parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def bool_setting(name, default=False):
    value = getattr(settings, name, None)
    if value is None:
        value = os.getenv(name)
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    return bool(default)
