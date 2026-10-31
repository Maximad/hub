"""Editorial slot resolution always reuses public source gates; no result caching."""
from dataclasses import dataclass, replace
from datetime import datetime

from django.urls import reverse

from core.models import HomepageFeature
from core.services.public_discovery import popup_item, program_item, public_popups, public_programs
from core.services.public_homepage import current_events, public_event


@dataclass(frozen=True)
class PublicHero:
    title: str
    url: str
    source_kind: str
    summary: str = ''
    image_url: str = ''
    image_alt: str = ''
    starts_at: datetime | None = None
    is_ongoing: bool = False


def source_hero(item, kind):
    return PublicHero(title=item.title, url=item.url, source_kind=kind,
                      image_url=item.image_url, image_alt=item.image_alt,
                      starts_at=item.starts_at, is_ongoing=getattr(item, 'is_ongoing', False))


def resolve_homepage_feature(*, now, fallback_event=None, preview_feature=None):
    feature = preview_feature or HomepageFeature.objects.select_related('image').first()
    reason = 'not_configured'
    if feature:
        reason = 'disabled'
        if preview_feature is not None or (feature.enabled and feature.is_published):
            reason = 'outside_schedule'
            in_window = ((feature.starts_at is None or feature.starts_at <= now)
                         and (feature.ends_at is None or now < feature.ends_at))
            if preview_feature is not None or in_window:
                hero = None
                if feature.source_kind == 'event' and feature.event_id:
                    event = current_events(now).filter(pk=feature.event_id).first()
                    if event:
                        hero = source_hero(public_event(event, now=now), 'event')
                elif feature.source_kind == 'program' and feature.program_id:
                    program = public_programs(now).filter(pk=feature.program_id).first()
                    if program:
                        hero = source_hero(program_item(program), 'program')
                elif feature.source_kind == 'participation' and feature.participation_id:
                    popup = public_popups(now).filter(pk=feature.participation_id).first()
                    if popup:
                        hero = source_hero(popup_item(popup), 'participation')
                elif feature.source_kind == 'evergreen':
                    hero = PublicHero(title='هَبّ السويداء', url=reverse('public_event_list'), source_kind='evergreen')
                if hero is not None:
                    image_url = ''
                    if feature.image_id and feature.image.is_active and feature.image.media_type in {'image', 'gif'}:
                        image_url = feature.image.safe_url
                    return replace(hero, summary=feature.summary_ar,
                                   image_url=image_url or hero.image_url,
                                   image_alt=(feature.image.alt_text_ar or hero.title) if image_url else hero.image_alt), 'selected'
                reason = 'ineligible_source'
    if fallback_event:
        return source_hero(fallback_event, 'event'), reason
    return None, reason
