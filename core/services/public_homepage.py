"""Read-only, allowlisted public presentation data. No operational models escape."""
from dataclasses import dataclass
from django.conf import settings
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, Q, Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from events.models import Event, EventMedia, EventPerformance

DAMASCUS = ZoneInfo('Asia/Damascus')


@dataclass(frozen=True)
class PublicPerformance:
    uuid: object
    starts_at: datetime
    ends_at: datetime | None
    status: str


@dataclass(frozen=True)
class PublicEvent:
    title: str
    description: str
    url: str
    starts_at: datetime
    ends_at: datetime | None
    image_url: str
    image_alt: str
    is_ongoing: bool
    is_past: bool
    performances: tuple[PublicPerformance, ...] = ()


def published_events():
    # No publication history exists: cancelled/completed/private records are 404,
    # even for someone holding a formerly valid URL. Never infer prior approval.
    media = EventMedia.objects.filter(
        is_active=True, media_asset__is_active=True,
        media_asset__media_type__in=[MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF],
        role__in=[EventMedia.Role.COVER, EventMedia.Role.POSTER],
    ).select_related('media_asset').order_by('-is_primary', 'sort_order', 'pk')
    return Event.objects.filter(status=Event.Status.PUBLISHED, is_public=True).prefetch_related(
        Prefetch('media', queryset=media, to_attr='public_media'),
        Prefetch('performances', queryset=EventPerformance.objects.order_by('starts_at'),
                 to_attr='public_performances'),
    )


def current_events(now):
    all_shows = EventPerformance.objects.filter(event_id=OuterRef('pk'))
    active_shows = all_shows.filter(status=EventPerformance.Status.SCHEDULED).filter(
        Q(starts_at__gte=now) | Q(starts_at__lt=now, ends_at__gt=now))
    return published_events().annotate(
        has_shows=Exists(all_shows), has_active_show=Exists(active_shows),
    ).filter(
        Q(has_active_show=True)
        | Q(has_shows=False, starts_at__gte=now)
        | Q(has_shows=False, starts_at__lt=now, ends_at__gt=now)
    ).order_by('starts_at', 'pk')


def public_event(event, *, now):
    image_url = image_alt = ''
    for item in event.public_media:
        asset = item.media_asset
        # safe_url checks file existence and rejects unsafe schemes. Do not use
        # thumbnail_url: generating derivatives would mutate storage on a GET.
        url = asset.safe_url
        if url:
            image_url = url
            image_alt = asset.alt_text_ar or event.title_ar
            break
    shows = tuple(PublicPerformance(
        uuid=show.uuid, starts_at=timezone.localtime(show.starts_at, DAMASCUS),
        ends_at=timezone.localtime(show.ends_at, DAMASCUS) if show.ends_at else None,
        status=show.status,
    ) for show in event.public_performances if show.status == EventPerformance.Status.SCHEDULED)
    upcoming = next((show for show in shows if show.starts_at >= timezone.localtime(now, DAMASCUS)), None)
    display_show = upcoming or (shows[-1] if shows else None)
    first_start = display_show.starts_at if display_show else timezone.localtime(event.starts_at, DAMASCUS)
    first_end = display_show.ends_at if display_show else (
        timezone.localtime(event.ends_at, DAMASCUS) if event.ends_at else None
    )
    return PublicEvent(
        title=event.title_ar, description=event.description_ar,
        url=reverse('public_event_detail', kwargs={'uuid': event.uuid}),
        starts_at=first_start, ends_at=first_end,
        image_url=image_url, image_alt=image_alt,
        is_ongoing=bool(first_start < now and first_end and first_end > now),
        is_past=bool(first_end <= now if first_end else first_start < now),
        performances=shows,
    )


def public_homepage_context(*, now=None, preview_feature=None):
    now = now or timezone.now()
    events = tuple(public_event(event, now=now) for event in current_events(now)[:3])
    from core.services.public_discovery import public_discovery_context
    from core.services.homepage_feature import resolve_homepage_feature
    hero, reason = resolve_homepage_feature(now=now, fallback_event=events[0] if events else None,
                                          preview_feature=preview_feature)
    discovery = public_discovery_context(now=now)
    return {'events': events, 'featured_event': events[0] if events else None, 'hero': hero,
            'feature_resolution': reason, 'public_homepage_live': settings.PUBLIC_HOMEPAGE_ENABLED,
            'nav_programs': bool(discovery['programs']), 'nav_popups': bool(discovery['popups']),
            'nav_memberships': bool(discovery['memberships']), **discovery}
