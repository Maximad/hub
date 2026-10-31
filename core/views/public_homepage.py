"""Anonymous discovery only. All transactional entry points remain unchanged."""
from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from core.services.public_homepage import (
    current_events, public_event, public_homepage_context, published_events,
)
from core.views import menu


def require_public_release():
    if not settings.PUBLIC_HOMEPAGE_ENABLED:
        raise Http404


@never_cache
@require_safe
def homepage(request):
    if not settings.PUBLIC_HOMEPAGE_ENABLED:
        return menu.dashboard(request)
    return render(request, 'public/homepage.html', public_homepage_context())


@never_cache
@require_safe
def event_list(request):
    require_public_release()
    now = timezone.now()
    page = Paginator(current_events(now), 12).get_page(request.GET.get('page'))
    events = tuple(public_event(event, now=now) for event in page.object_list)
    return render(request, 'public/events.html', {'events': events, 'page': page})


@never_cache
@require_safe
def event_detail(request, uuid):
    require_public_release()
    event = get_object_or_404(published_events(), uuid=uuid)
    return render(request, 'public/event_detail.html', {
        'event': public_event(event, now=timezone.now()),
    })
