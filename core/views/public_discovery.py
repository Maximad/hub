"""Read-only programs, pop-ups, and membership discovery behind the public release gate."""
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from core.services.public_discovery import (
    membership_item, popup_item, program_item, public_membership_candidates,
    public_popups, public_programs,
)
from core.views.public_homepage import require_public_release


SECTIONS = {
    'programs': ('البرامج', 'برامج معلنة ومفتوحة، وتفاصيل المشاركة عند نشرها.', 'public_program_list'),
    'popups': ('الضيوف والتجارب', 'مشاركات معلنة وتجارب ضيافة ولقاء.', 'public_popup_list'),
    'memberships': ('العضويات', 'تعرّف على الخطط المعروضة ووصفها وأسعارها الحالية.', 'public_membership_list'),
}


def discovery_page(request, queryset, mapper, section):
    title, intro, index_route = SECTIONS[section]
    page = Paginator(queryset, 12).get_page(request.GET.get('page'))
    items = tuple(item for record in page.object_list if (item := mapper(record)) is not None)
    return render(request, 'public/discovery_list.html', {
        'items': items, 'page': page, 'section': section, 'section_title': title,
        'intro': intro, 'index_route': index_route,
    })


def detail_page(request, item, section):
    if item is None:
        raise Http404
    title, _, index_route = SECTIONS[section]
    return render(request, 'public/discovery_detail.html', {
        'item': item, 'section': section, 'section_title': title, 'index_route': index_route,
    })


@never_cache
@require_safe
def program_list(request):
    require_public_release()
    return discovery_page(request, public_programs(timezone.now()), program_item, 'programs')


@never_cache
@require_safe
def program_detail(request, code):
    require_public_release()
    return detail_page(request, program_item(get_object_or_404(public_programs(timezone.now()), code=code)), 'programs')


@never_cache
@require_safe
def popup_list(request):
    require_public_release()
    return discovery_page(request, public_popups(timezone.now()), popup_item, 'popups')


@never_cache
@require_safe
def popup_detail(request, uuid):
    require_public_release()
    return detail_page(request, popup_item(get_object_or_404(public_popups(timezone.now()), uuid=uuid)), 'popups')


@never_cache
@require_safe
def membership_list(request):
    require_public_release()
    return discovery_page(request, public_membership_candidates(), membership_item, 'memberships')


@never_cache
@require_safe
def membership_detail(request, code):
    require_public_release()
    return detail_page(request, membership_item(get_object_or_404(public_membership_candidates(), code=code)), 'memberships')
