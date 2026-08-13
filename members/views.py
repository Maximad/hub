from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from accounts.permissions import user_has_capability
from core.models import ActivityLog, Member
from members.models import MemberDeviceToken
from members.services import consume_activation_token, create_activation_token, resolve_member_from_request, validate_activation_token


def _redirect_destination(request):
    destination = request.POST.get('next', '') if request.method == 'POST' else request.GET.get('next', '')
    destination = destination.strip()
    if destination.startswith('//') or not url_has_allowed_host_and_scheme(
        destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return reverse('menu_public')
    return destination


@require_http_methods(['GET', 'HEAD', 'POST'])
def activate_member_device(request, token):
    destination = _redirect_destination(request)
    if request.method in ('GET', 'HEAD'):
        activation = validate_activation_token(token)
        if not activation:
            messages.error(request, 'رابط التفعيل غير صالح أو منتهي الصلاحية.')
            return redirect('menu_public')
        response = render(request, 'members/activation_confirm.html', {
            'member': activation.member,
            'destination': destination,
        })
        response.headers['Cache-Control'] = 'no-store'
        # Keep the one-time token URL out of cross-origin Referer headers while
        # retaining the same-origin Referer Django needs for HTTPS CSRF checks.
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    if request.POST.get('confirm') != 'yes':
        messages.error(request, 'يرجى تأكيد تفعيل العضوية.')
        return redirect('menu_public')

    label = request.META.get('HTTP_USER_AGENT', '').split(')')[0][:120]
    device, cookie_value = consume_activation_token(token, label)
    if not device:
        messages.error(request, 'رابط التفعيل غير صالح أو منتهي الصلاحية.')
        return redirect('menu_public')
    messages.success(request, 'تم تفعيل عضويتك على هذا الجهاز.')
    response = redirect(destination)
    response.set_cookie(settings.MEMBER_DEVICE_COOKIE_NAME, cookie_value,
        max_age=settings.MEMBER_DEVICE_COOKIE_AGE, httponly=True,
        secure=settings.MEMBER_DEVICE_COOKIE_SECURE, samesite='Lax', path='/')
    ActivityLog.objects.create(action='member_device_activated', details={'member_public_code': str(device.member.public_code), 'device_uuid': str(device.uuid)})
    return response


@require_POST
def deactivate_member_device(request):
    destination = _redirect_destination(request)
    context = resolve_member_from_request(request, touch=False)
    if context:
        MemberDeviceToken.objects.filter(pk=context.device.pk).update(revoked_at=timezone.now())
        ActivityLog.objects.create(action='member_device_revoked', details={'member_public_code': str(context.member.public_code), 'device_uuid': str(context.device.uuid)})
    response = redirect(destination)
    response.delete_cookie(settings.MEMBER_DEVICE_COOKIE_NAME, path='/', samesite='Lax')
    return response


@login_required
@require_POST
def generate_activation(request, member_id):
    if not user_has_capability(request.user, 'members/internet'):
        raise Http404()
    member = get_object_or_404(Member, public_code=member_id)
    _, raw = create_activation_token(member, request.user)
    path = reverse('member_activate', kwargs={'token': raw})
    base = settings.PUBLIC_BASE_URL.rstrip('/') + '/' if settings.PUBLIC_BASE_URL else request.build_absolute_uri('/')
    request.session['member_activation_url'] = urljoin(base, path.lstrip('/'))
    return redirect('staff_member_detail', member_id=member.public_code)


@login_required
@require_POST
def revoke_device(request, member_id, device_id=None):
    if not user_has_capability(request.user, 'members/internet'):
        raise Http404()
    member = get_object_or_404(Member, public_code=member_id)
    devices = member.device_tokens.filter(revoked_at__isnull=True)
    if device_id:
        devices = devices.filter(uuid=device_id)
    devices.update(revoked_at=timezone.now())
    return redirect('staff_member_detail', member_id=member.public_code)


@login_required
def activation_qr(request, member_id):
    if not user_has_capability(request.user, 'members/internet'):
        raise Http404()
    url = request.session.get('member_activation_url')
    if not url:
        raise Http404()
    import qrcode
    import qrcode.image.svg
    response = HttpResponse(content_type='image/svg+xml')
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(response)
    return response
