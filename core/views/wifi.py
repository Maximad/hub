"""Customer-facing entry page used by the Hub Wi-Fi captive flow."""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse

from catalog.models import MediaAsset
from core.models import ActivityLog, HubVisit, InternetSession
from core.services.hotspot_connect import one_tap_session_connect_configured
from core.services.table_visit_access import resolve_table_number
from core.services.visit_internet import customer_packages, metered_customer_error, self_service_enabled
from core.services.visit_internet_devices import active_browser_session
from core.services.visits import (
    issue_visit_credential,
    resolve_visit_credential,
    set_visit_cookie,
)
from core.settings_helpers import get_system_settings
from core.views.visits import _absolute_destination, _hotspot_relay_response
from internet.guest_wifi import (
    get_guest_wifi_policy,
    guest_wifi_code_required,
    guest_wifi_daily_minutes_remaining,
    guest_wifi_grants_remaining,
    guest_wifi_policy_error,
    prepare_guest_wifi_session_network,
    start_guest_wifi_session,
)
from internet.session_network_backends import PROVISIONED
from members.services import resolve_member_from_request
from members.benefits import resolve_internet_price


logger = logging.getLogger(__name__)
WIFI_PORTAL_HEADER_MEDIA_KEY = 'wifi_portal_header'


def _validation_message(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _wifi_internet_path():
    return reverse('wifi_entry') + '?mode=internet'


def _menu_path(visit):
    if visit and visit.table_id:
        return reverse('menu_table', kwargs={'qr_token': visit.table.qr_token}) + '?view=menu'
    return reverse('menu_public')


def _session_network_ready(session):
    if not session:
        return False
    if session.network_provider != InternetSession.NetworkProvider.MIKROTIK:
        return True
    return session.network_status == PROVISIONED


def _visual_media_url(media):
    if not media or not getattr(media, 'is_active', False) or not getattr(media, 'is_visual_media', False):
        return ''
    return media.safe_url or ''


def _portal_header_media():
    """Use the newest active MediaAsset explicitly marked for the captive portal."""
    return (
        MediaAsset.objects.filter(
            is_active=True,
            media_type__in=[MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF],
            title_en=WIFI_PORTAL_HEADER_MEDIA_KEY,
        )
        .order_by('-created_at', '-pk')
        .first()
    )


def _attach_member_to_visit(visit, member_context, *, source):
    if not member_context:
        return visit
    if visit.member_id is None:
        visit.member = member_context.member
        visit.save(update_fields=['member', 'updated_at'])
        ActivityLog.objects.create(action='visit.member_auto_attached', details={
            'visit_id': visit.pk,
            'member_id': member_context.member.pk,
            'source': source,
        })
        return visit
    if visit.member_id != member_context.member.pk:
        raise ValidationError('تعذر مطابقة الحساب مع جلسة هذا الجهاز.')
    return visit


def _ensure_wifi_visit(request, *, source='wifi_access'):
    """Return a browser-bound Hub visit without requiring a table or membership."""
    member_context = resolve_member_from_request(request)
    credential = resolve_visit_credential(request)
    raw_cookie = None

    if credential:
        visit = _attach_member_to_visit(credential.visit, member_context, source=source)
        return visit, credential, raw_cookie, member_context

    visit = HubVisit.objects.create(
        table=None,
        member=member_context.member if member_context else None,
        notes=source,
    )
    credential, raw_cookie = issue_visit_credential(visit)
    ActivityLog.objects.create(action='visit.created', details={
        'visit_id': visit.pk,
        'source': source,
    })
    ActivityLog.objects.create(action='visit.browser_bound', details={
        'visit_id': visit.pk,
        'source': source,
    })
    return visit, credential, raw_cookie, member_context


def _open_internet_options(request):
    """Open the existing Internet storefront for a visitor or member, table optional."""
    if not self_service_enabled(get_system_settings()):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect(_wifi_internet_path())
    try:
        _visit, _credential, raw_cookie, _member_context = _ensure_wifi_visit(
            request,
            source='wifi_internet_options',
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect(_wifi_internet_path())
    response = redirect(reverse('current_visit') + '?focus=internet')
    return set_visit_cookie(response, raw_cookie) if raw_cookie else response


def _start_guest_wifi(request):
    """Create/reuse a browser-bound visit and authorize bounded basic access."""
    system_settings = get_system_settings()
    policy = get_guest_wifi_policy()
    if not self_service_enabled(system_settings):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect(_wifi_internet_path())
    policy_error = guest_wifi_policy_error(policy)
    if policy_error:
        messages.error(request, policy_error)
        return redirect(_wifi_internet_path())

    try:
        visit, credential, raw_cookie, member_context = _ensure_wifi_visit(
            request,
            source='guest_wifi',
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect(_wifi_internet_path())

    try:
        session, created = start_guest_wifi_session(
            request=request,
            visit=visit,
            credential=credential,
            member_context=member_context,
            venue_code=request.POST.get('venue_code', ''),
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        response = redirect(_wifi_internet_path())
        return set_visit_cookie(response, raw_cookie) if raw_cookie else response
    except Exception:
        logger.exception('Guest Wi-Fi authorization failed')
        messages.error(request, 'تعذر بدء الإنترنت حالياً. يمكنك طلب المساعدة من الفريق.')
        response = redirect(_wifi_internet_path())
        return set_visit_cookie(response, raw_cookie) if raw_cookie else response

    network_ready = prepare_guest_wifi_session_network(session)
    if network_ready:
        messages.success(
            request,
            'تم تفعيل الإنترنت الأساسي.' if created else 'الإنترنت الأساسي ما يزال فعالاً على هذا الجهاز.',
        )
    else:
        messages.warning(
            request,
            'سُجل طلب الإنترنت الأساسي، لكن الشبكة لم تؤكد الجاهزية بعد. أعد المحاولة بعد لحظات.',
        )

    if network_ready and one_tap_session_connect_configured(session):
        try:
            return _hotspot_relay_response(
                request,
                session,
                destination_url=_absolute_destination(request, _menu_path(visit)),
                raw_cookie=raw_cookie,
            )
        except Exception:
            logger.exception('Guest Wi-Fi HotSpot relay failed for session_id=%s', session.pk)
            messages.warning(request, 'تم تجهيز الجلسة، لكن تعذر الاتصال التلقائي بالشبكة.')

    response = redirect(_wifi_internet_path())
    return set_visit_cookie(response, raw_cookie) if raw_cookie else response


def wifi_entry(request):
    """Render the stable Hub-owned captive landing page.

    The landing page deliberately presents only two primary choices: Internet or
    menu. Opening the Internet sheet is read-only. RouterOS remains responsible for
    the physical HotSpot session and this view never changes router configuration.
    """
    if request.method == 'POST':
        action = request.POST.get('wifi_action')
        if action == 'start_guest_wifi':
            return _start_guest_wifi(request)
        if action == 'internet_options':
            return _open_internet_options(request)

    raw_number = request.GET.get('table_number', '').strip()
    table_number_error = ''

    if raw_number:
        try:
            table = resolve_table_number(raw_number)
        except ValidationError as exc:
            table_number_error = _validation_message(exc)
        else:
            return redirect('menu_table', qr_token=table.qr_token)

    credential = resolve_visit_credential(request, touch=False)
    visit = credential.visit if credential else None
    current_table = visit.table if visit and visit.table_id else None
    current_table_url = ''
    if current_table is not None:
        current_table_url = (
            reverse('menu_table', kwargs={'qr_token': current_table.qr_token})
            + '?view=menu'
        )
    menu_url = _menu_path(visit)

    member_context = resolve_member_from_request(request, touch=False)
    internet_member = visit.member if visit and visit.member_id else (
        member_context.member if member_context else None
    )
    system_settings = get_system_settings()
    policy = get_guest_wifi_policy()
    guest_error = guest_wifi_policy_error(policy)
    guest_available = bool(not guest_error and self_service_enabled(system_settings))
    internet_options_available = self_service_enabled(system_settings)
    metered_error = metered_customer_error(system_settings, internet_member) if internet_options_available else 'غير متاح'
    packages = customer_packages(internet_member) if internet_options_available else []
    first_package = packages[0] if packages else None
    if first_package:
        first_package.customer_price_syp = int(resolve_internet_price(
            internet_member, first_package,
        )[0])

    active_session = active_browser_session(credential) if credential else None
    active_guest_session = None
    active_fast_session = None
    if active_session and hasattr(active_session, 'guest_wifi_grant'):
        active_guest_session = active_session
    elif active_session:
        active_fast_session = active_session

    if credential:
        daily_remaining = guest_wifi_daily_minutes_remaining(credential, policy)
        basic_can_start = bool(guest_wifi_grants_remaining(credential, policy))
    else:
        daily_remaining = min(
            int(policy.session_minutes or 0),
            int(policy.daily_complimentary_minutes or 0),
        )
        basic_can_start = True

    internet_panel_open = bool(
        request.GET.get('mode') == 'internet'
        or active_guest_session
        or active_fast_session
    )
    header_media = _portal_header_media()
    brand_logo_media = getattr(system_settings, 'brand_logo_media', None)
    can_customize_portal = bool(
        request.user.is_authenticated
        and (request.user.is_superuser or getattr(request.user, 'role', '') == 'admin')
    )

    response = render(request, 'menu/wifi_entry.html', {
        'table_number_error': table_number_error,
        'table_number_value': raw_number,
        'current_visit': visit,
        'current_table': current_table,
        'current_table_url': current_table_url,
        'menu_url': menu_url,
        'internet_panel_open': internet_panel_open,
        'came_from_free_access': request.GET.get('free') == '1' and bool(active_guest_session),
        'member_context': member_context,
        'internet_options_available': internet_options_available,
        'internet_metered_available': not metered_error,
        'internet_metered_rate_syp': int(system_settings.default_rate_per_hour_syp or 0),
        'first_internet_package': first_package,
        'guest_wifi_available': guest_available,
        'guest_wifi_unavailable_reason': guest_error or '',
        'guest_wifi_code_required': (
            guest_wifi_code_required(policy, member_context, credential)
            if guest_available else False
        ),
        'guest_wifi_initial_minutes': int(policy.session_minutes or 0),
        'guest_wifi_daily_minutes_remaining': daily_remaining,
        'guest_wifi_can_start': basic_can_start,
        'guest_wifi_order_bonus_enabled': bool(policy.order_bonus_enabled),
        'guest_wifi_order_bonus_minutes': int(policy.order_bonus_minutes or 0),
        'active_guest_wifi_session': active_guest_session,
        'active_fast_wifi_session': active_fast_session,
        'active_guest_wifi_network_ready': _session_network_ready(active_guest_session),
        'active_fast_wifi_network_ready': _session_network_ready(active_fast_session),
        'wifi_portal_header_url': _visual_media_url(header_media),
        'wifi_portal_header_alt': header_media.display_alt_text if header_media else '',
        'brand_logo_url': _visual_media_url(brand_logo_media),
        'can_customize_portal': can_customize_portal,
        'wifi_portal_media_admin_url': (
            reverse('admin:catalog_mediaasset_add')
            + '?title_ar=%D8%B5%D9%88%D8%B1%D8%A9+%D9%87%D9%8A%D8%AF%D8%B1+%D8%A8%D9%88%D8%A7%D8%A8%D8%A9+%D8%A7%D9%84%D8%B2%D9%88%D8%A7%D8%B1'
            + '&title_en=wifi_portal_header&media_type=image'
        ) if can_customize_portal else '',
    })
    response['Cache-Control'] = 'no-store, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response
