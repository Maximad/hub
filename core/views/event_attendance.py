"""Anonymous browser-owned requests and capability-protected staff review."""
import secrets
import uuid as uuidlib

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import MaxValueValidator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.permissions import require_staff_capability
from core.services.public_homepage import public_event, published_events
from core.views.public_homepage import require_public_release
from django.utils import timezone
from reservations.attendance import (
    RequestRateLimited, accepting_events, digest, normalize_phone, review_request, submit_request, withdraw_request,
)
from reservations.models import EventAttendanceRequest as AttendanceRequest

OWNER_SESSION_KEY = 'public_attendance_owner'


class AttendanceForm(forms.Form):
    name = forms.CharField(label='الاسم', max_length=120)
    phone = forms.RegexField(label='رقم التواصل', regex=r'^\+?[0-9٠-٩۰-۹ ()-]{5,30}$', max_length=30,
                            error_messages={'invalid': 'أدخل رقم تواصل صالحاً.'}, widget=forms.TextInput(attrs={'type': 'tel'}))
    party_size = forms.IntegerField(label='عدد الأشخاص', min_value=1)
    client_key = forms.UUIDField(widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['party_size'].max_value = settings.PUBLIC_ATTENDANCE_MAX_PARTY_SIZE
        self.fields['party_size'].validators.append(MaxValueValidator(settings.PUBLIC_ATTENDANCE_MAX_PARTY_SIZE))
        self.fields['party_size'].widget.attrs['max'] = settings.PUBLIC_ATTENDANCE_MAX_PARTY_SIZE

    def clean_phone(self):
        return normalize_phone(self.cleaned_data['phone'])


def owner_digest(request):
    token = request.session.get(OWNER_SESSION_KEY)
    return digest(token) if token else None


def _message(error):
    return ' '.join(error.messages)


@never_cache
@require_http_methods(['GET', 'POST'])
def attendance_form(request, uuid):
    require_public_release()
    event = get_object_or_404(published_events(), uuid=uuid)
    if request.method == 'GET' and not accepting_events().filter(pk=event.pk).exists():
        raise Http404
    form = AttendanceForm(request.POST if request.method == 'POST' else None,
                          initial={'party_size': 1, 'client_key': uuidlib.uuid4()})
    status = 200
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['website']:
            form.add_error(None, 'تعذر إرسال الطلب.')
            status = 400
        else:
            owner_token = request.session.get(OWNER_SESSION_KEY) or secrets.token_urlsafe(32)
            ip_hash = salted_hmac('public-attendance-ip', request.META.get('REMOTE_ADDR', 'unknown'), algorithm='sha256').hexdigest()
            try:
                row = submit_request(event_uuid=event.uuid, owner_hash=digest(owner_token), ip_hash=ip_hash,
                                     **{key: form.cleaned_data[key] for key in ['client_key', 'name', 'phone', 'party_size']})
            except RequestRateLimited:
                form.add_error(None, 'وصلت للحد المؤقت للطلبات. حاول لاحقاً.')
                status = 429
            except ValidationError as error:
                form.add_error(None, _message(error))
                status = 409
            else:
                if not request.session.get(OWNER_SESSION_KEY):
                    request.session[OWNER_SESSION_KEY] = owner_token
                return redirect('public_attendance_receipt', uuid=row.uuid)
    elif request.method == 'POST':
        status = 400
    return render(request, 'public/attendance_form.html', {'event': public_event(event, now=timezone.now()), 'form': form}, status=status)


@never_cache
@require_http_methods(['GET', 'POST'])
def attendance_receipt(request, uuid):
    require_public_release()
    owner = owner_digest(request)
    if not owner:
        raise Http404
    row = get_object_or_404(AttendanceRequest.objects.select_related('reservation'), uuid=uuid, owner_hash=owner)
    error = ''
    if request.method == 'POST':
        if request.POST.get('action') != 'withdraw':
            raise Http404
        try:
            withdraw_request(row.uuid, owner_hash=owner)
        except ValidationError as exc:
            error = _message(exc)
            row.refresh_from_db()
        else:
            return redirect('public_attendance_receipt', uuid=row.uuid)
    label = row.get_status_display()
    if row.status == 'approved':
        if row.reservation_id:
            label = {'confirmed': 'الفريق أكّد حجز الحضور', 'cancelled': 'ألغى الفريق الحجز',
                     'completed': 'انتهى الحضور', 'no_show': 'سُجّل عدم حضور', 'pending': 'الحجز بانتظار مراجعة الفريق'}.get(row.reservation.status, 'راجع الفريق')
        else:
            label = 'راجع الفريق للتحقق من حالة الحجز'
    receipt = {'title': row.event_title, 'party_size': row.party_size, 'status_label': label,
               'can_withdraw': row.status == 'pending', 'approved': row.status == 'approved'}
    response = render(request, 'public/attendance_receipt.html', {'receipt': receipt, 'error': error}, status=409 if error else 200)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@require_staff_capability('reservations')
@never_cache
@require_http_methods(['GET'])
def staff_attendance_requests(request):
    rows = AttendanceRequest.objects.select_related('event', 'reservation').order_by('-created_at', '-pk')
    state = request.GET.get('status', 'pending')
    if state in AttendanceRequest.Status.values:
        rows = rows.filter(status=state)
    else:
        state = 'all'
    return render(request, 'staff/attendance_requests.html', {
        'page': Paginator(rows, 25).get_page(request.GET.get('page')), 'state': state, 'states': AttendanceRequest.Status.choices,
    })


@require_staff_capability('reservations')
@never_cache
@require_http_methods(['GET', 'POST'])
def staff_attendance_request(request, uuid):
    row = get_object_or_404(AttendanceRequest.objects.select_related('event', 'reservation', 'reviewed_by'), uuid=uuid)
    if request.method == 'POST':
        try:
            review_request(row.uuid, actor=request.user, action=request.POST.get('action'), reason=request.POST.get('reason', ''))
        except ValidationError as error:
            messages.error(request, _message(error))
        else:
            messages.success(request, 'تمت معالجة طلب الحضور.')
        return redirect('staff_attendance_request', uuid=row.uuid)
    return render(request, 'staff/attendance_request.html', {'attendance': row})
