from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.permissions import user_has_capability
from core.models import ActivityLog, HubVisit, Order
from member_accounts.card import issue_member_card_token, member_card_max_age_seconds, resolve_member_card_token
from member_accounts.identity import resolve_member_identity


def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def _public_url(request, path):
    base = settings.PUBLIC_BASE_URL.rstrip('/') + '/' if settings.PUBLIC_BASE_URL else request.build_absolute_uri('/')
    return urljoin(base, path.lstrip('/'))


def _staff_can_scan_member(user):
    return bool(
        user.is_authenticated
        and (
            user_has_capability(user, 'pos')
            or user_has_capability(user, 'members/internet')
        )
    )


def _member_card_scan_url(request, token):
    return _public_url(
        request,
        reverse('staff_member_card_scan', kwargs={'token': token}),
    )


@require_http_methods(['GET', 'HEAD'])
def member_card_qr(request):
    """Render a fresh short-lived QR for an already authenticated member browser."""
    identity = resolve_member_identity(request)
    if identity is None:
        return HttpResponse(status=401)

    token = issue_member_card_token(identity.member)
    scan_url = _member_card_scan_url(request, token)
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return HttpResponse('QR renderer unavailable.', status=503, content_type='text/plain; charset=utf-8')

    response = HttpResponse(content_type='image/svg+xml')
    qrcode.make(scan_url, image_factory=qrcode.image.svg.SvgPathImage).save(response)
    response.headers['X-Member-Card-Max-Age'] = str(member_card_max_age_seconds())
    return _no_store(response)


@login_required
@require_http_methods(['GET', 'POST'])
def staff_member_card_scan(request, token):
    """Resolve a short-lived card and optionally attach the member to an open visit."""
    if not _staff_can_scan_member(request.user):
        raise Http404()

    card = resolve_member_card_token(token)
    if card is None:
        response = render(request, 'member_accounts/staff_card_scan.html', {
            'invalid': True,
        }, status=400)
        return _no_store(response)

    error = ''
    selected_visit = None
    if request.method == 'POST':
        raw_visit = (request.POST.get('visit') or '').strip()
        try:
            with transaction.atomic():
                selected_visit = (
                    HubVisit.objects.select_for_update(of=('self',))
                    .select_related('table', 'member')
                    .filter(public_code=raw_visit, status=HubVisit.Status.OPEN)
                    .first()
                )
                if selected_visit is None:
                    raise ValueError('الجلسة غير موجودة أو مغلقة.')
                if selected_visit.member_id and selected_visit.member_id != card.member.pk:
                    raise ValueError('هذه الجلسة مرتبطة بعضو آخر.')
                conflicting_order = (
                    selected_visit.orders.exclude(status=Order.Status.CANCELLED)
                    .exclude(member__isnull=True)
                    .exclude(member=card.member)
                    .exists()
                )
                if conflicting_order:
                    raise ValueError('أحد طلبات الجلسة مرتبط بعضو آخر؛ لم يتم تغيير الهوية.')

                if selected_visit.member_id is None:
                    selected_visit.member = card.member
                    selected_visit.save(update_fields=['member', 'updated_at'])
                linked_orders = (
                    selected_visit.orders.exclude(status=Order.Status.CANCELLED)
                    .filter(member__isnull=True)
                    .update(member=card.member)
                )
                ActivityLog.objects.create(
                    actor=request.user,
                    action='member_account.card_attached_to_visit',
                    details={
                        'member_public_code': str(card.member.public_code),
                        'visit_id': selected_visit.pk,
                        'linked_orders': linked_orders,
                    },
                )
        except ValueError as exc:
            error = str(exc)
        else:
            messages.success(request, f'تم ربط {card.member.name_ar} بهذه الجلسة.')
            return redirect('staff_visit_detail', public_code=selected_visit.public_code)

    visits = (
        HubVisit.objects.filter(status=HubVisit.Status.OPEN)
        .select_related('table', 'table__room', 'member')
        .order_by('-last_activity_at', '-id')[:20]
    )
    response = render(request, 'member_accounts/staff_card_scan.html', {
        'invalid': False,
        'card': card,
        'member': card.member,
        'active_membership': card.membership,
        'visits': visits,
        'error': error,
        'card_max_age_seconds': member_card_max_age_seconds(),
    }, status=400 if error else 200)
    return _no_store(response)
