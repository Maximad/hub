import logging
import uuid
from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import ActivityLog, HubVisit, InternetEntitlement, InternetPackage, InternetSession, TableArea
from core.services.hotspot_connect import (
    build_hotspot_login_payload,
    build_session_hotspot_login_payload,
    one_tap_connect_configured,
    one_tap_session_connect_configured,
)
from core.services.internet_access import end_usage_session
from core.services.visit_internet import (
    create_visit_internet_sale_and_start,
    customer_packages,
    finalize_visit_metered_session,
    metered_network_activated_at,
    prepare_visit_metered_session_network,
    self_service_enabled,
    start_existing_visit_entitlement,
    start_visit_metered_session,
    usable_member_entitlements,
)
from core.services.visits import issue_visit_credential, resolve_visit_credential, set_visit_cookie
from core.settings_helpers import get_system_settings
from members.benefits import resolve_internet_price
from members.services import resolve_member_from_request

logger = logging.getLogger(__name__)


def _error_text(error):
    if isinstance(error, ValidationError):
        return next(iter(error.messages), 'تعذر بدء الإنترنت. يمكنك طلب المساعدة من الفريق.')
    return 'تعذر بدء الإنترنت. يمكنك طلب المساعدة من الفريق.'


def _decorate_session_network_state(session):
    session.network_ready = bool(
        session.entitlement_id
        or session.network_provider != InternetSession.NetworkProvider.MIKROTIK
        or metered_network_activated_at(session) is not None
    )
    session.one_tap_connect_available = bool(
        one_tap_connect_configured(session.entitlement)
        if session.entitlement_id
        else one_tap_session_connect_configured(session)
    )
    return session


def _internet_context(visit=None, member=None):
    packages = customer_packages(member)
    for package in packages:
        package.customer_price_syp = int(resolve_internet_price(member, package)[0])
    entitlements = usable_member_entitlements(visit) if visit else InternetEntitlement.objects.none()
    sessions = list(visit.internet_sessions.select_related('entitlement', 'package')
                    .order_by('-start_time')) if visit else []
    for session in sessions:
        _decorate_session_network_state(session)
    return {'internet_packages': packages, 'internet_entitlements': entitlements,
            'internet_sessions': sessions, 'internet_request_key': uuid.uuid4()}


def current_visit(request):
    system_settings = get_system_settings()
    if not system_settings.customer_visits_enabled:
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        return redirect('menu_public')
    visit = credential.visit
    orders = visit.orders.exclude(status='cancelled').prefetch_related(
        'items', 'discounts', 'payments').order_by('-created_at', '-id')
    table_entry_url = (
        reverse('menu_table', kwargs={'qr_token': visit.table.qr_token})
        if visit.table_id else reverse('menu_public')
    )
    menu_url = table_entry_url + '?view=menu' if visit.table_id else table_entry_url
    active_internet_session = visit.internet_sessions.select_related('package', 'entitlement').filter(
        status=InternetSession.Status.ACTIVE).order_by('-start_time').first()
    if active_internet_session:
        _decorate_session_network_state(active_internet_session)
    context = {'visit': visit, 'orders': orders, 'menu_url': menu_url,
               'table_entry_url': table_entry_url,
               'active_internet_session': active_internet_session,
               'internet_self_service_enabled': self_service_enabled(system_settings)}
    if context['internet_self_service_enabled']:
        context.update(_internet_context(visit, visit.member))
    return render(request, 'menu/current_visit.html', context)


def _current_visit_destination(request):
    path = reverse('current_visit')
    base = (getattr(settings, 'PUBLIC_BASE_URL', '') or '').strip()
    if base:
        return urljoin(base.rstrip('/') + '/', path.lstrip('/'))
    return request.build_absolute_uri(path)


@require_POST
def visit_internet_purchase_start(request):
    system_settings = get_system_settings()
    if not self_service_enabled(system_settings):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    raw_cookie = None
    try:
        mode = request.POST.get('mode', 'package').strip()
        table = (TableArea.objects.filter(qr_token=request.POST.get('table')).first()
                 if request.POST.get('table') else None)
        credential = resolve_visit_credential(request)
        member_context = resolve_member_from_request(request)
        package = None
        session = None
        metered_created = False
        if mode != 'metered':
            package = get_object_or_404(InternetPackage, public_code=request.POST.get('package'))

        with transaction.atomic():
            if credential:
                visit = credential.visit
                if table and visit.table_id != table.pk:
                    raise ValidationError('الجلسة مرتبطة بطاولة أخرى. يرجى طلب المساعدة من الفريق.')
            else:
                if not table:
                    raise ValidationError('الجلسة مغلقة.')
                visit = HubVisit.objects.create(table=table,
                    member=member_context.member if member_context else None)
                credential, raw_cookie = issue_visit_credential(visit)
                ActivityLog.objects.create(action='visit.created', details={
                    'visit_id': visit.pk, 'source': 'public_internet'})
                ActivityLog.objects.create(action='visit.browser_bound', details={'visit_id': visit.pk})
            member = visit.member
            if member_context and visit.member_id is None:
                visit.member = member = member_context.member
                visit.save(update_fields=['member', 'updated_at'])
                ActivityLog.objects.create(action='visit.member_auto_attached', details={
                    'visit_id': visit.pk, 'member_id': member.pk})
            elif member_context and visit.member_id != member_context.member.pk:
                raise ValidationError('تعذر مطابقة العضوية. يرجى طلب المساعدة من الفريق.')

            if mode == 'metered':
                session, metered_created = start_visit_metered_session(
                    visit=visit,
                    credential=credential,
                    member=member,
                    guest_phone=request.POST.get('guest_phone', ''),
                )
            else:
                create_visit_internet_sale_and_start(
                    visit=visit,
                    credential=credential,
                    package=package,
                    request_key=request.POST.get('request_key', ''),
                    member=member,
                )

        if mode == 'metered':
            # Never contact the router from inside the commercial transaction.
            # New manual sessions are processed too so they use the same durable
            # activation semantics without changing production behavior while the
            # MikroTik integration flag remains disabled.
            network_ready = True
            if metered_created or session.network_provider == InternetSession.NetworkProvider.MIKROTIK:
                network_ready = prepare_visit_metered_session_network(session)
            if network_ready:
                messages.success(request, 'بدأ الإنترنت حسب الوقت. يبدأ الاحتساب من لحظة جاهزية الشبكة.')
            else:
                messages.warning(
                    request,
                    'يجري تجهيز اتصال الإنترنت. لن يبدأ احتساب الوقت قبل أن تصبح الشبكة جاهزة.',
                )
        else:
            messages.success(request, 'بدأت جلسة الإنترنت.')

        if request.POST.get('next') == 'menu' and table:
            target = reverse('menu_table', kwargs={'qr_token': table.qr_token}) + '?view=menu&internet_started=1'
            response = redirect(target)
        else:
            response = redirect('current_visit')
        return set_visit_cookie(response, raw_cookie) if raw_cookie else response
    except (ValidationError, InternetPackage.DoesNotExist) as exc:
        messages.error(request, _error_text(exc))
        return redirect(request.META.get('HTTP_REFERER') or reverse('menu_public'))
    except Exception:
        logger.exception('Customer visit Internet purchase/start failed')
        messages.error(request, 'تعذر بدء الإنترنت. يمكنك طلب المساعدة من الفريق.')
        return redirect(request.META.get('HTTP_REFERER') or reverse('menu_public'))


@require_POST
def visit_internet_entitlement_start(request, public_code):
    if not self_service_enabled(get_system_settings()):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        messages.error(request, 'الجلسة مغلقة.')
        return redirect('menu_public')
    try:
        entitlement = get_object_or_404(InternetEntitlement, public_code=public_code)
        start_existing_visit_entitlement(visit=credential.visit, credential=credential,
                                         entitlement=entitlement)
        messages.success(request, 'بدأت جلسة الإنترنت.')
    except ValidationError as exc:
        messages.error(request, _error_text(exc))
    return redirect('current_visit')


@require_POST
def visit_internet_session_connect(request, public_code):
    """Relay an authorized current-visit browser into its RouterOS HotSpot login."""
    if not self_service_enabled(get_system_settings()):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        messages.error(request, 'الجلسة مغلقة.')
        return redirect('menu_public')
    session = get_object_or_404(
        InternetSession.objects.select_related('entitlement'),
        public_code=public_code,
        visit=credential.visit,
        status=InternetSession.Status.ACTIVE,
    )
    try:
        if session.entitlement_id:
            payload = build_hotspot_login_payload(
                session.entitlement,
                destination_url=_current_visit_destination(request),
            )
        else:
            payload = build_session_hotspot_login_payload(
                session,
                destination_url=_current_visit_destination(request),
            )
    except Exception as exc:
        logger.warning(
            'Customer HotSpot relay unavailable for session_id=%s entitlement_id=%s',
            session.pk,
            session.entitlement_id,
        )
        messages.error(request, _error_text(exc))
        return redirect('current_visit')

    ActivityLog.objects.create(action='visit.internet_connect_relay_issued', details={
        'visit_id': credential.visit_id,
        'session_id': session.pk,
        'entitlement_id': session.entitlement_id,
        'network_provider': session.network_provider,
    })
    response = render(request, 'menu/hotspot_connect.html', payload)
    response['Cache-Control'] = 'no-store, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        f"form-action {payload['login_origin']}; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@require_POST
def visit_internet_session_stop(request, public_code):
    if not self_service_enabled(get_system_settings()):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        messages.error(request, 'الجلسة مغلقة.')
        return redirect('menu_public')
    session = get_object_or_404(InternetSession, public_code=public_code,
                                visit=credential.visit,
                                status=InternetSession.Status.ACTIVE)
    try:
        if session.entitlement_id:
            ended = end_usage_session(session)
            message = 'تم إيقاف استخدام الإنترنت. وقت الباقة المحددة غير المستخدم لا يُستعاد.'
        else:
            ended = finalize_visit_metered_session(session)
            if (ended.status == InternetSession.Status.CANCELLED
                    and ended.lifecycle_end_reason == 'network_not_activated'):
                message = 'أُلغيت جلسة الإنترنت دون أي احتساب لأن تجهيز الشبكة لم يكتمل.'
            else:
                message = f'تم إنهاء الإنترنت. أضيف {int(ended.payable_total_syp or 0)} ل.س إلى حساب جلستك.'
    except ValidationError as exc:
        messages.error(request, _error_text(exc))
        return redirect('current_visit')
    ActivityLog.objects.create(action='visit.internet_session_ended', details={
        'visit_id': credential.visit_id, 'session_id': ended.pk,
        'entitlement_id': ended.entitlement_id,
    })
    messages.success(request, message)
    return redirect('current_visit')
