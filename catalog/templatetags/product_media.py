from django import template

register = template.Library()


@register.simple_tag
def product_card_image(media, width=320):
    if not media:
        return ''
    helper = getattr(media, 'card_image_url', None)
    if callable(helper):
        return helper(width=width)
    return getattr(media, 'resolved_url', '') or ''


@register.simple_tag
def product_card_srcset(media):
    if not media:
        return ''
    helper = getattr(media, 'card_image_srcset', None)
    if callable(helper):
        return helper()
    url = getattr(media, 'resolved_url', '') or ''
    return f'{url} 320w' if url else ''
