from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models


absolute_url_validator = URLValidator(schemes=['http', 'https'])


def validate_media_or_absolute_url(value):
    if not value:
        return

    media_url = getattr(settings, 'MEDIA_URL', '/media/')
    if media_url and value.startswith(media_url):
        return

    try:
        absolute_url_validator(value)
    except ValidationError as exc:
        raise ValidationError(
            'Enter an absolute http(s) URL or a media path starting with %(media_url)s.',
            params={'media_url': media_url},
        ) from exc


class RelativeOrAbsoluteURLField(models.URLField):
    default_validators = [validate_media_or_absolute_url]

    def formfield(self, **kwargs):
        defaults = {'form_class': forms.CharField, 'max_length': self.max_length}
        defaults.update(kwargs)
        return models.Field.formfield(self, **defaults)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        path = 'django.db.models.URLField'
        return name, path, args, kwargs
