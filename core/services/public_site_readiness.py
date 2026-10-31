"""Aggregate, read-only checks of the connected public-site configuration.

Reuse publication and commercial gates. Never serialize editorial copy, contacts,
credentials or the commercial assessor's internal findings into this report.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import HomepageFeature
from core.services.homepage_feature import resolve_homepage_feature
from core.services.public_discovery import (
    popup_item, public_membership_candidates, public_popups, public_programs,
)
from core.services.public_homepage import DAMASCUS, current_events, public_event
from events.models import Event
from members.plan_readiness import assess_membership_plan
from reservations.attendance import accepting_events
from reservations.models import EventAttendanceRequest


def public_site_readiness_report(*, now=None, limit=100):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError('limit must be an integer between 1 and 500.')
    now = now or timezone.now()
    checks = []
    metrics = {}

    def add(code, warning, message, resolution='No action required.'):
        checks.append(dict(code=code, status='WARN' if warning else 'PASS',
                           message=message, resolution=resolution))

    def sample(queryset, code):
        total = queryset.count()
        rows = list(queryset[:limit])
        metrics[code] = dict(total=total, inspected=len(rows), truncated=total > len(rows))
        add(code + '_coverage', total > len(rows),
            f'Inspected {len(rows)} of {total}; per-section limit {limit}.',
            'Raise --limit (maximum 500) or review the remaining records separately.'
            if total > len(rows) else 'No action required.')
        return rows

    enabled = bool(settings.PUBLIC_HOMEPAGE_ENABLED)
    add('release_gate', not enabled,
        'Public homepage enabled in this process.' if enabled else 'Public homepage disabled in this process.',
        'Keep disabled until the intended environment and content have been reviewed.' if not enabled
        else 'Verify the running web service separately; CLI settings do not prove its configuration.')

    events = sample(current_events(now), 'events')
    event_items = [public_event(event, now=now) for event in events]
    popups = sample(public_popups(now), 'popups')
    popup_items = [popup_item(popup) for popup in popups]
    metrics['programs'] = dict(total=public_programs(now).count())
    add('public_content', False,
        f"Eligible events: {metrics['events']['total']}; programs: {metrics['programs']['total']}; "
        f"pop-ups: {metrics['popups']['total']}. Empty sections are supported.")

    hero, reason = resolve_homepage_feature(now=now, fallback_event=event_items[0] if event_items else None)
    metrics['feature'] = dict(reason=reason, rendered_source=hero.source_kind if hero else 'evergreen_fallback')
    add('homepage_feature', reason == 'ineligible_source',
        f"Feature resolution: {reason}; rendered source: {metrics['feature']['rendered_source']}.",
        'Review the selected source publication and dates; safe fallback is already active.'
        if reason == 'ineligible_source' else 'Disabled, unscheduled and unconfigured slots use the normal safe fallback.')

    missing_event_media = sum(bool(event.public_media) and not item.image_url
                              for event, item in zip(events, event_items))
    missing_popup_media = sum(bool(popup.vendor.public_media) and not item.image_url
                              for popup, item in zip(popups, popup_items))
    feature = HomepageFeature.objects.select_related('image').first() if reason == 'selected' else None
    unusable_feature_image = bool(feature and feature.image_id and not (
        feature.image.is_active and feature.image.media_type in {'image', 'gif'} and feature.image.safe_url
    ))
    metrics['media'] = dict(events_without_usable_configured_media=missing_event_media,
                           popups_without_usable_configured_media=missing_popup_media,
                           selected_feature_image_unusable=unusable_feature_image)
    missing_media = bool(missing_event_media or missing_popup_media or unusable_feature_image)
    add('public_media', missing_media,
        f'Inspected records with configured public media but no usable image: '
        f'events {missing_event_media}; pop-ups {missing_popup_media}; '
        f'selected feature override {int(unusable_feature_image)}. Text-only fallback is supported.',
        'Review approved active artwork and local file availability. External URLs are not fetched.'
        if missing_media else 'No action required.')

    plans = sample(public_membership_candidates(), 'memberships')
    findings = [assess_membership_plan(plan) for plan in plans]
    blocked = sum(not finding['can_sell'] for finding in findings)
    advisory = sum(bool(finding['warnings']) for finding in findings)
    metrics['memberships'].update(blocked=blocked, advisory=advisory)
    add('membership_configuration', bool(blocked or advisory),
        f'Inspected customer-visible plans: blocked {blocked}; advisory {advisory}. '
        'Plans with blocking configuration remain hidden by the public gate.',
        'Review membership_readiness with authorized staff; no sale or provisioning was attempted.'
        if blocked or advisory else 'No action required.')

    accepting = accepting_events(now=now)
    eligible = accepting.count()
    future_opted_in = Event.objects.filter(accepts_attendance_requests=True, starts_at__gt=now).count()
    metrics['intake'] = dict(eligible_events=eligible, future_opted_in_events=future_opted_in,
                             future_opted_in_but_closed=future_opted_in - eligible)
    add('attendance_opt_ins', future_opted_in > eligible,
        f'Future opt-ins: {future_opted_in}; eligible intake events: {eligible}. '
        'Zero opt-ins supports a read-only public site.',
        'Review future opted-in events that are private or unpublished; their intake remains closed.'
        if future_opted_in > eligible else 'No action required.')

    pending = EventAttendanceRequest.objects.filter(status=EventAttendanceRequest.Status.PENDING)
    queue = pending.aggregate(
        pending=Count('pk'),
        older_than_24h=Count('pk', filter=Q(created_at__lt=now - timedelta(hours=24))),
    )
    # exclude() includes deleted/null event links, which cannot be approved.
    queue['event_not_accepting'] = pending.exclude(event_id__in=accepting.values('pk')).count()
    queue['approved_without_reservation'] = EventAttendanceRequest.objects.filter(
        status=EventAttendanceRequest.Status.APPROVED, reservation__isnull=True,
    ).count()
    metrics['requests'] = queue
    attention = queue['older_than_24h'] or queue['event_not_accepting'] or queue['approved_without_reservation']
    add('attendance_queue', bool(attention),
        f"Pending: {queue['pending']}; older than 24h: {queue['older_than_24h']}; "
        f"event no longer accepting: {queue['event_not_accepting']}; "
        f"approved without reservation: {queue['approved_without_reservation']}.",
        'Review the staff attendance queue. The 24h threshold is a review hint, not an expiry rule. '
        'This report does not approve, reject, withdraw or reserve seats.' if attention else 'No action required.')

    if eligible or queue['pending'] or queue['approved_without_reservation']:
        users = sample(get_user_model().objects.filter(is_active=True).select_related('access_profile').order_by('pk'),
                       'reviewers')
        authorized = sum(user_has_capability(user, 'reservations') for user in users)
        metrics['reviewers']['authorized_in_sample'] = authorized
        add('attendance_reviewers', not authorized,
            f'Active users with effective reservations capability in inspected sample: {authorized}.',
            'Review staff access profiles and per-user overrides; remaining users may be outside the sample.'
            if not authorized else 'No action required.')

    counts = {level: sum(row['status'] == level for row in checks) for level in ('PASS', 'WARN', 'FAIL')}
    return dict(
        status='WARN' if counts['WARN'] else 'PASS', counts=counts, checks=checks, metrics=metrics,
        read_only=True, release_enabled=enabled, checked_at=timezone.localtime(now, DAMASCUS).isoformat(),
        timezone='Asia/Damascus', limit=limit,
        scope='current_command_settings_and_connected_database_and_storage',
        limitations=[
            'Counts and samples can change during the report; this is not a locked snapshot or seat guarantee.',
            'No deployed revision, running web service, HTTP routes or external image URLs are verified.',
            'Copy, artwork approval and staging browser review require human review; PASS is not release approval.',
            'Media checks use the configured storage existence check and safe URL logic; no thumbnails are generated.',
        ],
    )
