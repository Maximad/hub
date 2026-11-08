"""Arabic-first staff event workspace.

Presentation-only extension of the existing event staff views. Keep creation and
reservation mutations on their established, permission-checked services.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from core.views_legacy import _assert_staff_capability, staff_event_new
from events.models import Event, EventMedia
from reservations.models import Reservation
from vendors.models import VendorParticipation


def _cover_media():
    """Fetch approved-to-display staff cover/poster assets without per-card queries."""
    return Prefetch(
        'media',
        queryset=EventMedia.objects.filter(
            is_active=True,
            media_asset__is_active=True,
            media_asset__media_type__in=('image', 'gif'),
            role__in=(EventMedia.Role.COVER, EventMedia.Role.POSTER),
        ).select_related('media_asset').order_by(
            '-is_primary', 'role', 'sort_order', 'pk',
        ),
        to_attr='staff_media',
    )


@login_required
def staff_events(request):
    _assert_staff_capability(request.user, 'events')
    events = (
        Event.objects.select_related('room')
        .prefetch_related(_cover_media())
        .order_by('starts_at', '-created_at')
    )
    return render(request, 'staff/events.html', {
        'events': events,
        'public_homepage_enabled': settings.PUBLIC_HOMEPAGE_ENABLED,
    })


@login_required
def staff_event_detail(request, event_id):
    _assert_staff_capability(request.user, 'events')
    event = get_object_or_404(
        Event.objects.select_related('room').prefetch_related(
            _cover_media(), 'ticket_types',
        ),
        pk=event_id,
    )
    reservations = list(
        Reservation.objects.filter(event=event)
        .select_related('table_area')
        .order_by('reservation_date', 'start_time', 'pk')
    )
    participations = VendorParticipation.objects.filter(
        event=event,
    ).select_related('vendor', 'location_area', 'event').order_by('starts_at')
    legacy_participations = VendorParticipation.objects.filter(
        event__isnull=True,
        notes__icontains=event.title_ar,
    ).select_related('vendor', 'location_area').order_by('starts_at')
    return render(request, 'staff/event_detail.html', {
        'event': event,
        'reservations': reservations,
        'reservation_count': len(reservations),
        'confirmed_guests': sum(row.party_size for row in reservations if row.status == Reservation.Status.CONFIRMED),
        'pending_guests': sum(row.party_size for row in reservations if row.status == Reservation.Status.PENDING),
        'participations': participations,
        'legacy_participations': legacy_participations,
        'public_homepage_enabled': settings.PUBLIC_HOMEPAGE_ENABLED,
    })
