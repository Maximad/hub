"""Small, controlled Lucide icon subset (ISC License)."""
from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# Paths are from Lucide (https://lucide.dev). Names, not SVG, are accepted from templates.
ICONS = {
    'search': '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    'shopping-cart': '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.1 2H5l2.7 13.4a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.6L21 7H6"/>',
    'plus': '<path d="M5 12h14M12 5v14"/>',
    'minus': '<path d="M5 12h14"/>',
    'settings': '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
    'check': '<path d="m20 6-11 11-5-5"/>',
    'alert-triangle': '<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/><path d="M12 9v4M12 17h.01"/>',
    'printer': '<path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v8H6z"/>',
}

@register.simple_tag(takes_context=True)
def hub_icon(context, name, size='normal'):
    if not context.get('hub_icons_enabled', True) or name not in ICONS:
        return ''
    size_class = {'small': ' hub-icon--small', 'large': ' hub-icon--large'}.get(size, '')
    return format_html('<svg class="hub-icon{}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{}</svg>', size_class, mark_safe(ICONS[name]))
