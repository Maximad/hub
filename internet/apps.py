from django.apps import AppConfig


class InternetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'internet'

    def ready(self):
        from . import signals  # noqa: F401
