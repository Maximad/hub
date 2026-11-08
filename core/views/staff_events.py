"""Arabic-first staff event workspace.

Presentation-only extension of the existing event staff views. Keep creation and
reservation mutations on their established, permission-checked services.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import redirect

from accounts.permissions import require_staff_capability
from reservations.admissions import set_event_admission_total
from reservations.models import EventAdmission

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


@require_staff_capability('reservations')
def staff_event_checkin(request, event_id):
    """Ticket-only admission desk. No HubVisit or restaurant account is created."""
    event = get_object_or_404(Event, pk=event_id)
    if request.method == 'POST':
        raw_id = request.POST.get('reservation_id', '')
        booking = get_object_or_404(
            Reservation, pk=raw_id if raw_id.isdecimal() else None,
            event=event, reservation_type=Reservation.ReservationType.EVENT,
        )
        try:
            _row, changed = set_event_admission_total(
                booking.pk,
                request.POST.get('arrived_count', ''),
                actor=request.user,
            )
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(
                request,
                'تم تسجيل الوصول.' if changed else 'الحضور مسجّل مسبقاً؛ لم تُضف أعداد مكررة.',
            )
        return redirect('staff_event_checkin', event_id=event.pk)

    query = (request.GET.get('q') or '').strip()[:80]
    bookings = (
        Reservation.objects.filter(
            event=event, reservation_type=Reservation.ReservationType.EVENT,
        )
        .select_related('event_admission')
        .order_by('name', 'pk')
    )
    if query:
        bookings = bookings.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    page = Paginator(bookings, 30).get_page(request.GET.get('page'))
    summary = Reservation.objects.filter(
        event=event, reservation_type=Reservation.ReservationType.EVENT,
        status=Reservation.Status.CONFIRMED,
    ).aggregate(guests=Sum('party_size'))
    arrived = EventAdmission.objects.filter(
        reservation__event=event,
    ).aggregate(guests=Sum('arrived_count'))['guests'] or 0
    return render(request, 'staff/event_checkin.html', {
        'event': event,
        'page': page,
        'query': query,
        'confirmed_guests': summary['guests'] or 0,
        'arrived_guests': arrived,
    })
