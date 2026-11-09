from pathlib import Path
import re
import posixpath
from django.db.models import Q
from django.http import Http404
from django.utils.cache import patch_vary_headers
from accounts.permissions import user_has_capability
from catalog.models import MediaAsset

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
    # Match the same canonical path that Django's static view will open.
    path = posixpath.normpath(path).lstrip('/')
    source_path = re.sub(r'-card-\d+w\.webp$', '', path)
    assets = MediaAsset.objects.filter(Q(file=path) | Q(external_url='/media/' + path))
    if source_path != path:
        assets = MediaAsset.objects.filter(Q(file=path) | Q(file__startswith=source_path + '.'))
    private = assets.filter(Q(expense_receipts__isnull=False) | Q(purchase_receipts__isnull=False)).exists()
    if private and not user_has_capability(request.user, 'finance'):
        raise Http404()
    response = serve(
        request,
        path,
        document_root=settings.MEDIA_ROOT,
        show_indexes=show_indexes,
    )
    content_type = FONT_CONTENT_TYPES.get(Path(path).suffix.lower())
    if content_type:
        response['Content-Type'] = content_type
    if private:
        response['Cache-Control'] = 'private, no-store'
        patch_vary_headers(response, ('Cookie',))
    return response

