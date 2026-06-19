from django.utils.html import format_html


def media_asset_url(obj):
    if not obj:
        return ''
    if hasattr(obj, 'resolved_url'):
        return obj.resolved_url or ''
    if hasattr(obj, 'media_asset') and obj.media_asset_id:
        return obj.media_asset.url or ''
    return getattr(obj, 'url', '') or ''


def media_asset_alt(obj):
    if not obj:
        return ''
    return getattr(obj, 'display_alt_text', '') or getattr(obj, 'title_ar', '') or ''


def safe_media_preview(obj, width=72, ratio='2 / 3'):
    url = media_asset_url(obj)
    is_visual = getattr(obj, 'is_visual_media', False)
    if callable(is_visual):
        is_visual = is_visual()
    if not is_visual and getattr(obj, 'media_asset_id', None):
        is_visual = obj.media_asset.is_visual_media
    if url and is_visual:
        return format_html(
            '<a href="{}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;justify-content:center;width:{}px;aspect-ratio:{};overflow:hidden;border-radius:8px;background:#f8f5ef;">'
            '<img src="{}" alt="{}" style="width:100%;height:100%;object-fit:cover;object-position:center center;display:block;" />'
            '</a>',
            url,
            width,
            ratio,
            url,
            media_asset_alt(obj),
        )
    if url:
        return format_html('<a href="{}" target="_blank" rel="noopener">فتح الملف</a>', url)
    return '—'
