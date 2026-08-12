from django import template
from django.contrib.messages import constants


register = template.Library()


@register.simple_tag
def message_role(message):
    """Return an appropriate live-region role for a Django message."""
    return "alert" if message.level >= constants.ERROR else "status"
