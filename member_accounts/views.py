from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from accounts.permissions import user_has_capability
from core.models import ActivityLog, InternetSession, Member
from member_accounts.identity import resolve_member_identity
from member_accounts.models import MemberAccount, MemberInvitation, MemberLoginChallenge
from member_accounts.services import (
    claim_invitation,
    create_invitation,
    request_login_challenge,
    validate_invitation,
    verify_login_challenge,
)


def _public_url(request, path):
    base = settings.PUBLIC_BASE_URL.rstrip('/') + '/' if settings.PUBLIC_BASE_URL else request.build_absolute_uri('/')
    return urljoin(base, path.lstrip('/'))


def _set_member_cookie(response, value):
    response.set_cookie(
        settings.MEMBER_DEVICE_COOKIE_NAME,
        value,
        max_age=settings.MEMBER_DEVICE_COOKIE_AGE,
        httponly=True,
        secure=settings.MEMBER_DEVICE_COOKIE_SECURE,
        samesite='Lax',
        path='/',
    )
    return response


def _staff_can_manage_members(user):
    return user.is_authenticated and user_has_capability(user, 'members/internet')


def _safe_next(request, candidate):
    candidate = (candidate or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return ''


def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@require_http_methods(['GET', 'HEAD', 'POST'])
def join_invitation(request, token):
    invitation = validate_invitation(token)
    if request.method in ('GET', 'HEAD'):
        response = render(request, 'member_accounts/join.html', {
            'invitation': invitation,
            'invalid': invitation is None,
        })
        return _no_store(response)

    if invitation is None:
        return render(request, 'member_accounts/join.html', {
            'invalid': True,
        }, status=400)
    if request.POST.get('confirm') != 'yes':
        return render(request, 'member_accounts/join.html', {
            'invitation': invitation,
            'error': 'يرجى تأكيد إنشاء/تفعيل الحساب.',
        }, status=400)

    try:
        result = claim_invitation(
            token,
            name=request.POST.get('name', ''),
            device_label=request.META.get('HTTP_USER_AGENT', '').split(')')[0][:120],
        )
    except ValidationError as exc:
        return render(request, 'member_accounts/join.html', {
            'invitation': validate_invitation(token),
            'error': ' '.join(exc.messages),
        }, status=400)

    messages.success(request, 'تم تفعيل حساب هَب على هذا الجهاز.')
    response = redirect('member_account_home')
    return _set_member_cookie(response, result.cookie_value)


@require_http_methods(['GET', 'HEAD', 'POST'])
def member_login(request):
    identity = resolve_member_identity(request)
    if identity is not None:
        return redirect('member_account_home')

    next_path = _safe_next(request, request.GET.get('next') if request.method != 'POST' else request.POST.get('next'))
    error = ''
    if request.method == 'POST':
        phone = (request.POST.get('phone') or '').strip()
        try:
            result = request_login_challenge(
                phone,
                ip=request.META.get('REMOTE_ADDR', ''),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                next_path=next_path,
            )
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            response = redirect(
                'member_account_login_verify',
                challenge_id=result.challenge.uuid,
            )
            return _no_store(response)

    response = render(request, 'member_accounts/login.html', {
        'error': error,
        'next_path': next_path,
    })
    return _no_store(response)


@require_http_methods(['GET', 'HEAD', 'POST'])
def member_login_verify(request, challenge_id):
    challenge = MemberLoginChallenge.objects.filter(uuid=challenge_id).first()
    if challenge is None:
        raise Http404()

    error = ''
    if request.method == 'POST':
        try:
            result = verify_login_challenge(
                challenge_id,
                request.POST.get('code', ''),
                device_label=request.META.get('HTTP_USER_AGENT', '').split(')')[0][:120],
            )
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            destination = _safe_next(request, challenge.next_path) or reverse('member_account_home')
            messages.success(request, 'تم تسجيل الدخول إلى حساب هَب.')
            response = redirect(destination)
            return _set_member_cookie(response, result.cookie_value)

    response = render(request, 'member_accounts/login_verify.html', {
        'challenge': challenge,
        'error': error,
    })
    return _no_store(response)


def member_home(request):
    identity = resolve_member_identity(request)
    if identity is None:
        return redirect(f"{reverse('member_account_login')}?next={reverse('member_account_home')}")

    member = identity.member
    subscriptions = member.subscriptions.select_related('plan').order_by('-created_at')[:20]
    devices = member.device_tokens.filter(revoked_at__isnull=True).order_by('-created_at')
    sessions = InternetSession.objects.filter(member=member).select_related('package').order_by('-created_at')[:20]
    orders = member.orders.order_by('-created_at')[:20]
    return render(request, 'member_accounts/home.html', {
        'identity': identity,
        'account': identity.account,
        'member': member,
        'active_subscription': identity.active_subscription,
        'subscriptions': subscriptions,
        'devices': devices,
        'sessions': sessions,
        'orders': orders,
    })


@require_POST
def member_logout(request):
    identity = resolve_member_identity(request, touch=False)
    if identity:
        identity.device.revoked_at = timezone.now()
        identity.device.save(update_fields=['revoked_at'])
        ActivityLog.objects.create(
            action='member_account.logout',
            details={
                'member_public_code': str(identity.member.public_code),
                'device_uuid': str(identity.device.uuid),
            },
        )
    response = redirect('menu_public')
    response.delete_cookie(settings.MEMBER_DEVICE_COOKIE_NAME, path='/', samesite='Lax')
    return response


@login_required
def staff_invitation_new(request, member_id=None):
    if not _staff_can_manage_members(request.user):
        raise Http404()

    member = get_object_or_404(Member, public_code=member_id) if member_id else None
    invitation_url = ''
    error = ''
    if request.method == 'POST':
        invited_phone = (request.POST.get('phone') or (member.phone if member else '')).strip()
        invited_name = (request.POST.get('name') or (member.name_ar if member else '')).strip()
        existing = member or Member.objects.filter(phone__iexact=invited_phone).first()
        try:
            invitation, raw = create_invitation(
                member=existing,
                invited_phone=invited_phone,
                invited_name=invited_name,
                created_by=request.user,
                purpose=MemberInvitation.Purpose.ACCOUNT_CLAIM,
            )
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            invitation_url = _public_url(
                request,
                reverse('member_account_join', kwargs={'token': raw}),
            )
            member = existing

    return render(request, 'member_accounts/staff_invitation_new.html', {
        'member': member,
        'invitation_url': invitation_url,
        'error': error,
    })


@login_required
@require_POST
def staff_lock_account(request, member_id):
    if not _staff_can_manage_members(request.user):
        raise Http404()
    member = get_object_or_404(Member, public_code=member_id)
    account, _ = MemberAccount.objects.get_or_create(member=member)
    account.status = MemberAccount.Status.LOCKED
    account.save(update_fields=['status', 'updated_at'])
    member.device_tokens.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    ActivityLog.objects.create(
        actor=request.user,
        action='member_account.locked',
        details={'member_public_code': str(member.public_code)},
    )
    messages.success(request, 'تم قفل حساب العضو وإلغاء الأجهزة المفعّلة.')
    return redirect('staff_member_detail', member_id=member.public_code)
