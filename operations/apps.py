from django.apps import AppConfig
from django.conf import settings


class OperationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'operations'
    verbose_name = 'اليوم التشغيلي'

    def ready(self):
        from .config import bool_setting, int_setting

        defaults = {
            'BUSINESS_DAY_AUTO_OPEN_HOUR': int_setting('BUSINESS_DAY_AUTO_OPEN_HOUR', 10, minimum=0, maximum=23),
            'BUSINESS_DAY_CODE_REMINDER_MINUTES': int_setting('BUSINESS_DAY_CODE_REMINDER_MINUTES', 120, minimum=1),
            'BUSINESS_DAY_OPENING_REMINDER_MINUTES': int_setting('BUSINESS_DAY_OPENING_REMINDER_MINUTES', 90, minimum=1),
            'BUSINESS_DAY_CASH_DIFFERENCE_WARNING_SYP': int_setting('BUSINESS_DAY_CASH_DIFFERENCE_WARNING_SYP', 5000, minimum=0),
            'BUSINESS_DAY_DISCOUNT_WARNING_SYP': int_setting('BUSINESS_DAY_DISCOUNT_WARNING_SYP', 10000, minimum=0),
            'BUSINESS_DAY_CANCELLATION_WARNING_COUNT': int_setting('BUSINESS_DAY_CANCELLATION_WARNING_COUNT', 3, minimum=0),
        }
        if not hasattr(settings, 'BUSINESS_DAY_AUTO_OPEN_ENABLED'):
            settings.BUSINESS_DAY_AUTO_OPEN_ENABLED = bool_setting('BUSINESS_DAY_AUTO_OPEN_ENABLED', False)
        for name, value in defaults.items():
            if not hasattr(settings, name):
                setattr(settings, name, value)

        from . import signals  # noqa: F401
