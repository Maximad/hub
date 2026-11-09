"""Arabic-first staff event workspace.

Presentation-only extension of the existing event staff views. Keep creation and
reservation mutations on their established, permission-checked services.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
import csv
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import redirect

from accounts.permissions import require_staff_capability, is_owner_or_admin
from reservations.admissions import set_event_admission_total
from reservations.importing import preview_uploaded_csv, import_event_bookings
from events.models import EventTicketType
from reservations.models import EventAdmission
from reservations.reporting import event_booking_report

from core.views_legacy import _assert_staff_capability, staff_event_new
from events.models import Event, EventMedia
from events.finance import allocate_existing_event_expense, event_known_finance_report
from core.models import Expense
from django.core.exceptions import PermissionDenied
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
        'booking_report': event_booking_report(event.pk),
    })


@require_staff_capability('reservations')
def staff_event_checkin(request, event_id):
    """Ticket-only admission desk. No HubVisit or restaurant account is created."""
    event = get_object_or_404(Event, pk=event_id)
    if request.method == 'POST':
        raw_id = request.POST.get('reservation_id', '')
        booking = get_object_or_404(
            Reservation, pk=raw_id if raw_id.isdecimal() and len(raw_id) <= 15 else None,
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
        .select_related('event_admission', 'visit')
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


@require_staff_capability('reservations')
@require_http_methods(['GET', 'POST'])
def staff_event_booking_import(request, event_id):
    """Two-step upload preview/confirm. No customer PII stored in browser sessions."""
    event = get_object_or_404(Event, pk=event_id)
    tiers = list(EventTicketType.objects.filter(event=event, is_active=True).order_by('pk'))
    if request.method == 'GET' and request.GET.get('template') == '1':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="event-{event.pk}-bookings.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['name', 'phone', 'party_size', 'status', *[
            f'ticket_{tier.pk}' for tier in tiers
        ]])
        return response

    preview = []
    fingerprint = ''
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action', '')
        try:
            preview, fingerprint = preview_uploaded_csv(
                event.pk, request.FILES.get('bookings_csv'),
            )
            if action == 'confirm':
                confirmed_fingerprint = (request.POST.get('fingerprint') or '').strip()
                if not confirmed_fingerprint or fingerprint != confirmed_fingerprint:
                    raise ValidationError('الملف تغيّر بعد المعاينة؛ أعد مراجعة الملف.')
                batch, created = import_event_bookings(
                    event.pk, preview, fingerprint, actor=request.user,
                )
                messages.success(
                    request,
                    f'تم استيراد {batch.imported_count} حجزاً.' if created
                    else 'هذا الملف مستورد مسبقاً؛ لم يُنشأ أي حجز مكرر.',
                )
                return redirect('staff_event_detail', event_id=event.pk)
            if action != 'preview':
                raise ValidationError('إجراء غير صالح.')
        except ValidationError as problem:
            error = ' '.join(problem.messages)
            preview = []
            fingerprint = ''
    return render(request, 'staff/event_booking_import.html', {
        'event': event, 'tiers': tiers, 'preview': preview,
        'fingerprint': fingerprint, 'error': error,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def staff_event_finance(request, event_id):
    """Management-only attribution of already recorded expenses, no payments."""
    if not is_owner_or_admin(request.user):
        raise PermissionDenied('متابعة مالية الفعاليات للإدارة فقط.')
    event = get_object_or_404(Event, pk=event_id)
    if request.method == 'POST':
        raw_id = (request.POST.get('expense_id') or '').strip()
        try:
            if not raw_id.isdecimal() or len(raw_id) > 15:
                raise ValidationError('اختر مصروفاً موجوداً في حسابات هَب.')
            link, created = allocate_existing_event_expense(
                event.pk, int(raw_id), request.POST.get('amount_syp'),
                request.POST.get('rationale'), actor=request.user,
            )
            messages.success(
                request,
                'تم ربط المصروف بالفعالية دون إنشاء قبض أو صرف جديد.' if created
                else 'هذا الربط موجود مسبقاً؛ لم يسجّل مرة ثانية.',
            )
        except (ValidationError, Expense.DoesNotExist) as error:
            messages.error(
                request, ' '.join(error.messages) if isinstance(error, ValidationError)
                else 'المصروف المطلوب غير موجود.',
            )
        return redirect('staff_event_finance', event_id=event.pk)
    expenses = Expense.objects.filter(
        status__in=(Expense.Status.APPROVED, Expense.Status.PAID),
    ).select_related('category').order_by('-business_date', '-pk')[:100]
    return render(request, 'staff/event_finance.html', {
        'event': event,
        'report': event_known_finance_report(event.pk),
        'expenses': expenses,
    })
