from functools import lru_cache

from .models import PageSetting, SystemSetting


@lru_cache(maxsize=1)
def get_system_settings():
    settings = SystemSetting.objects.order_by('-updated_at', '-pk').first()
    if settings:
        return settings
    return SystemSetting.objects.create()


def get_page_setting(key, title_ar='', title_en='', subtitle_ar='', subtitle_en=''):
    system_settings = get_system_settings()
    page = PageSetting.objects.filter(key=key, is_active=True).first()
    if not page:
        return {
            'title': title_ar if system_settings.default_language == SystemSetting.Language.ARABIC else (title_en or title_ar),
            'subtitle': subtitle_ar if system_settings.default_language == SystemSetting.Language.ARABIC else (subtitle_en or subtitle_ar),
        }
    return {
        'title': page.title_for_language(system_settings.default_language, title_ar),
        'subtitle': page.subtitle_for_language(system_settings.default_language, subtitle_ar),
    }
