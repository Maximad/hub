from pathlib import Path

from django.conf import settings
from django.views.static import serve


FONT_CONTENT_TYPES = {
    '.woff2': 'font/woff2',
    '.woff': 'font/woff',
    '.otf': 'font/otf',
    '.ttf': 'font/ttf',
}


def serve_media(request, path, document_root=None, show_indexes=False):
    # Resolve MEDIA_ROOT at request time so runtime/test setting overrides are
    # honored instead of relying on the value captured when the URLconf loaded.
    response = serve(
        request,
        path,
        document_root=settings.MEDIA_ROOT,
        show_indexes=show_indexes,
    )
    content_type = FONT_CONTENT_TYPES.get(Path(path).suffix.lower())
    if content_type:
        response['Content-Type'] = content_type
    return response
