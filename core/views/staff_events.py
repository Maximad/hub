"""Arabic-first staff event workspace.

Presentation-only extension of the existing event staff views. Keep creation and
reservation mutations on their established, permission-checked services.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse, Http404
from django.views.decorators.http import require_http_methods
import csv
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import redirect

from accounts.permissions import require_staff_capability, is_owner_or_admin
from reservations.admissions import set_event_admission_total
from reservations.importing import preview_uploaded_csv, import_event_bookings
from events.models import EventTicketType, EventPerformance
from events.scheduling import add_performances, cancel_performance
from reservations.models import EventAdmission
from reservations.reporting import event_booking_report

from core.views_legacy import _assert_staff_capability, staff_event_new
from events.models import Event, EventMedia, EventLayoutSnapshot
from events.finance import allocate_existing_event_expense, event_known_finance_report
from core.models import Expense
from django.core.exceptions import PermissionDenied
from catalog.models import MediaAsset
from django.db import transaction
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
            _cover_media(), 'ticket_types', 'performances',
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
    show_rows = list(event.performances.all())
    totals = dict(
        Reservation.objects.filter(event=event,
            status__in=(Reservation.Status.PENDING, Reservation.Status.CONFIRMED))
        .values('performance_id').annotate(people=Sum('party_size'))
        .values_list('performance_id', 'people')
    )
    for show in show_rows:
        show.reserved_people = totals.get(show.pk, 0)
    return render(request, 'staff/event_detail.html', {
        'show_rows': show_rows,
        'event': event,
        'reservations': reservations,
        'reservation_count': len(reservations),
        'confirmed_guests': sum(row.party_size for row in reservations if row.status == Reservation.Status.CONFIRMED),
        'pending_guests': sum(row.party_size for row in reservations if row.status == Reservation.Status.PENDING),
        'participations': participations,
        'legacy_participations': legacy_participations,
        'public_homepage_enabled': settings.PUBLIC_HOMEPAGE_ENABLED,
        'booking_report': event_booking_report(event.pk),
        'has_seating_layout': EventLayoutSnapshot.objects.filter(event=event).exists(),
    })


@require_staff_capability('events')
@require_http_methods(['POST'])
def staff_event_schedule(request, event_id):
    """One short staff action for adding/cancelling shows, no duplicate event."""
    event = get_object_or_404(Event, pk=event_id)
    try:
        if request.POST.get('action') == 'cancel':
            show = get_object_or_404(EventPerformance, pk=request.POST.get('performance'),
                                     event=event)
            cancel_performance(show.pk, actor=request.user)
            messages.success(request, 'تم إلغاء الموعد دون حذف سجله.')
        else:
            start = parse_datetime(request.POST.get('starts_at', ''))
            end = parse_datetime(request.POST.get('ends_at', '')) if request.POST.get('ends_at') else None
            if not start:
                raise ValidationError('وقت البداية مطلوب وصيغته غير صالحة.')
            local = ZoneInfo('Asia/Damascus')
            if timezone.is_naive(start):
                start = timezone.make_aware(start, local)
            if end and timezone.is_naive(end):
                end = timezone.make_aware(end, local)
            raw_capacity = (request.POST.get('capacity') or '').strip()
            raw_count = (request.POST.get('count') or '1').strip()
            if raw_capacity and (not raw_capacity.isdecimal() or int(raw_capacity) <= 0):
                raise ValidationError('السعة يجب أن تكون عدداً موجباً.')
            if not raw_count.isdecimal():
                raise ValidationError('عدد العروض غير صالح.')
            shows = add_performances(
                event.pk, actor=request.user, starts_at=start, ends_at=end,
                capacity=int(raw_capacity) if raw_capacity else None,
                frequency=request.POST.get('frequency', 'once'),
                count=int(raw_count), room_id=request.POST.get('room') or None,
            )
            messages.success(request, f'تم حفظ {len(shows)} موعداً، دون إنشاء فعاليات مكررة.')
    except (ValidationError, ValueError, TypeError) as error:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'مدخلات غير صالحة.')
    return redirect('staff_event_detail', event_id=event.pk)


@require_staff_capability('reservations')
def staff_event_checkin(request, event_id):
    """Ticket-only admission desk. No HubVisit or restaurant account is created."""
    event = get_object_or_404(Event, pk=event_id)
    shows = list(EventPerformance.objects.filter(event=event).order_by('starts_at'))
    selected_id = request.POST.get('performance') if request.method == 'POST' else request.GET.get('performance')
    selected = next((show for show in shows if str(show.pk) == str(selected_id)), None)
    if selected_id and selected is None:
        raise Http404
    if not selected and len(shows) == 1:
        selected = shows[0]
    if request.method == 'POST':
        if len(shows) > 1 and not selected:
            messages.error(request, 'اختر موعد العرض أولاً.')
            return redirect('staff_event_checkin', event_id=event.pk)
        raw_id = request.POST.get('reservation_id', '')
        booking = get_object_or_404(
            Reservation, pk=raw_id if raw_id.isdecimal() and len(raw_id) <= 15 else None,
            event=event, reservation_type=Reservation.ReservationType.EVENT,
            **({'performance': selected} if selected else {}),
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
        url = 'staff_event_checkin'
        from django.urls import reverse
        location = reverse(url, kwargs={'event_id': event.pk})
        return redirect(f'{location}?performance={selected.pk}' if selected else location)

    query = (request.GET.get('q') or '').strip()[:80]
    bookings = (
        Reservation.objects.filter(
            event=event, reservation_type=Reservation.ReservationType.EVENT,
        )
        .filter(**({'performance': selected} if selected else
                   {'performance__isnull': True} if len(shows) > 1 else {}))
        .select_related('event_admission', 'visit', 'performance')
        .order_by('name', 'pk')
    )
    if query:
        bookings = bookings.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    page = Paginator(bookings, 30).get_page(request.GET.get('page'))
    summary = Reservation.objects.filter(
        event=event, reservation_type=Reservation.ReservationType.EVENT,
        status=Reservation.Status.CONFIRMED,
    )
    admissions = EventAdmission.objects.filter(reservation__event=event)
    if selected:
        summary = summary.filter(performance=selected)
        admissions = admissions.filter(reservation__performance=selected)
    elif len(shows) > 1:
        summary = summary.none()
        admissions = admissions.none()
    summary = summary.aggregate(guests=Sum('party_size'))
    arrived = admissions.aggregate(guests=Sum('arrived_count'))['guests'] or 0
    return render(request, 'staff/event_checkin.html', {
        'event': event,
        'performances': shows,
        'selected_performance': selected,
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
    performances = list(EventPerformance.objects.filter(
        event=event, status=EventPerformance.Status.SCHEDULED).order_by('starts_at'))
    performance_id = request.POST.get('performance') if request.method == 'POST' else request.GET.get('performance')
    selected_performance = next((row for row in performances if str(row.pk) == str(performance_id)), None)
    if performance_id and selected_performance is None:
        raise Http404
    if not selected_performance and len(performances) == 1:
        selected_performance = performances[0]
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
                performance_id=selected_performance.pk if selected_performance else None,
            )
            if action == 'confirm':
                confirmed_fingerprint = (request.POST.get('fingerprint') or '').strip()
                if not confirmed_fingerprint or fingerprint != confirmed_fingerprint:
                    raise ValidationError('الملف تغيّر بعد المعاينة؛ أعد مراجعة الملف.')
                batch, created = import_event_bookings(
                    event.pk, preview, fingerprint, actor=request.user,
                    performance_id=selected_performance.pk if selected_performance else None,
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
        'event': event, 'tiers': tiers, 'performances': performances,
        'selected_performance': selected_performance, 'preview': preview,
        'fingerprint': fingerprint, 'error': error,
    })

@require_staff_capability('events')
@require_http_methods(['GET', 'POST'])
def staff_event_artwork(request, event_id):
    """Upload cover/poster through the existing safe MediaAsset validator."""
    event = get_object_or_404(Event, pk=event_id)
    error = ''
    if request.method == 'POST':
        role = request.POST.get('role')
        upload = request.FILES.get('image')
        if role not in {EventMedia.Role.COVER, EventMedia.Role.POSTER}:
            error = 'اختر نوع الصورة (غلاف أو ملصق).'
        elif not upload:
            error = 'اختر ملف صورة لرفعه.'
        else:
            ext = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else ''
            if ext not in {'jpg', 'jpeg', 'png', 'webp', 'gif'}:
                error = 'يمكن رفع ملفات صور JPG وPNG وWebP وGIF فقط.'
            else:
                asset = MediaAsset(
                    title_ar=f'{event.title_ar} — صورة الفعالية'[:180],
                    alt_text_ar=(request.POST.get('alt_text_ar') or event.title_ar).strip()[:180],
                    file=upload,
                    media_type=MediaAsset.MediaType.GIF if ext == 'gif' else MediaAsset.MediaType.IMAGE,
                    uploaded_by=request.user,
                )
                try:
                    asset.full_clean()
                except ValidationError as problem:
                    error = ' '.join(problem.messages)
                else:
                    with transaction.atomic():
                        # Serialize concurrent uploads for the same event.
                        Event.objects.select_for_update().get(pk=event.pk)
                        asset.save()
                        EventMedia.objects.filter(
                            event=event, role__in=(EventMedia.Role.COVER, EventMedia.Role.POSTER),
                            is_primary=True,
                        ).update(is_primary=False)
                        EventMedia.objects.create(
                            event=event, media_asset=asset, role=role,
                            is_active=True, is_primary=True,
                        )
                    messages.success(request, 'تم رفع الصورة وربطها بالفعالية.')
                    return redirect('staff_event_detail', event_id=event.pk)
    media = EventMedia.objects.filter(event=event).select_related('media_asset').order_by('-is_primary', 'sort_order')
    return render(request, 'staff/event_artwork.html', {
        'event': event, 'event_media': media,
        'error': error,
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
    ).exclude(paid_from=Expense.PaidFrom.EXTERNAL).select_related('category').order_by('-business_date', '-pk')[:100]
    return render(request, 'staff/event_finance.html', {
        'event': event,
        'report': event_known_finance_report(event.pk),
        'expenses': expenses,
    })
