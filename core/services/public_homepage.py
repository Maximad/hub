"""Read-only, allowlisted public presentation data. No operational models escape."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from events.models import Event, EventMedia

DAMASCUS = ZoneInfo('Asia/Damascus')


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


def published_events():
    # No publication history exists: cancelled/completed/private records are 404,
    # even for someone holding a formerly valid URL. Never infer prior approval.
    media = EventMedia.objects.filter(
        is_active=True, media_asset__is_active=True,
        media_asset__media_type__in=[MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF],
        role__in=[EventMedia.Role.COVER, EventMedia.Role.POSTER],
    ).select_related('media_asset').order_by('-is_primary', 'sort_order', 'pk')
    return Event.objects.filter(status=Event.Status.PUBLISHED, is_public=True).prefetch_related(
        Prefetch('media', queryset=media, to_attr='public_media')
    )


def current_events(now):
    return published_events().filter(
        Q(starts_at__gte=now) | Q(starts_at__lt=now, ends_at__gt=now)
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
    return PublicEvent(
        title=event.title_ar, description=event.description_ar,
        url=reverse('public_event_detail', kwargs={'uuid': event.uuid}),
        starts_at=timezone.localtime(event.starts_at, DAMASCUS),
        ends_at=timezone.localtime(event.ends_at, DAMASCUS) if event.ends_at else None,
        image_url=image_url, image_alt=image_alt,
        is_ongoing=bool(event.starts_at < now and event.ends_at and event.ends_at > now),
        is_past=bool(event.ends_at <= now if event.ends_at else event.starts_at < now),
    )


def public_homepage_context(*, now=None):
    now = now or timezone.now()
    events = tuple(public_event(event, now=now) for event in current_events(now)[:3])
    return {'events': events, 'featured_event': events[0] if events else None}
