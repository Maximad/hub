"""Customer-facing entry page used by the Hub Wi-Fi captive flow."""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse

from core.models import ActivityLog, HubVisit, InternetSession
from core.services.hotspot_connect import one_tap_session_connect_configured
from core.services.table_visit_access import resolve_table_number
from core.services.visit_internet import self_service_enabled
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
    guest_wifi_grants_remaining,
    guest_wifi_policy_error,
    prepare_guest_wifi_session_network,
    start_guest_wifi_session,
)
from members.services import resolve_member_from_request


logger = logging.getLogger(__name__)


def _validation_message(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def _wifi_destination(request):
    return _absolute_destination(request, reverse('wifi_entry') + '?free=1')


def _start_guest_wifi(request):
    """Create/reuse an anonymous visit and authorize bounded complimentary access."""
    system_settings = get_system_settings()
    policy = get_guest_wifi_policy()
    if not self_service_enabled(system_settings):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('wifi_entry')
    policy_error = guest_wifi_policy_error(policy)
    if policy_error:
        messages.error(request, policy_error)
        return redirect('wifi_entry')

    credential = resolve_visit_credential(request)
    raw_cookie = None
    member_context = resolve_member_from_request(request)

    if credential:
        visit = credential.visit
        if member_context and visit.member_id is None:
            visit.member = member_context.member
            visit.save(update_fields=['member', 'updated_at'])
            ActivityLog.objects.create(action='visit.member_auto_attached', details={
                'visit_id': visit.pk,
                'member_id': member_context.member.pk,
                'source': 'guest_wifi',
            })
        elif member_context and visit.member_id != member_context.member.pk:
            messages.error(request, 'تعذر مطابقة الحساب مع جلسة هذا الجهاز.')
            return redirect('wifi_entry')
    else:
        visit = HubVisit.objects.create(
            table=None,
            member=member_context.member if member_context else None,
            notes='guest_wifi',
        )
        credential, raw_cookie = issue_visit_credential(visit)
        ActivityLog.objects.create(action='visit.created', details={
            'visit_id': visit.pk,
            'source': 'guest_wifi',
        })
        ActivityLog.objects.create(action='visit.browser_bound', details={
            'visit_id': visit.pk,
            'source': 'guest_wifi',
        })

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
        response = redirect('wifi_entry')
        return set_visit_cookie(response, raw_cookie) if raw_cookie else response
    except Exception:
        logger.exception('Guest Wi-Fi authorization failed')
        messages.error(request, 'تعذر بدء الإنترنت حالياً. يمكنك طلب المساعدة من الفريق.')
        response = redirect('wifi_entry')
        return set_visit_cookie(response, raw_cookie) if raw_cookie else response

    network_ready = prepare_guest_wifi_session_network(session)
    if network_ready:
        messages.success(
            request,
            'تم تفعيل إنترنت الزوار.' if created else 'جلسة الإنترنت ما تزال فعالة على هذا الجهاز.',
        )
    else:
        messages.warning(request, 'يجري تجهيز الاتصال. يمكنك المحاولة مجدداً بعد لحظات.')

    if network_ready and one_tap_session_connect_configured(session):
        try:
            return _hotspot_relay_response(
                request,
                session,
                destination_url=_wifi_destination(request),
                raw_cookie=raw_cookie,
            )
        except Exception:
            logger.exception('Guest Wi-Fi HotSpot relay failed for session_id=%s', session.pk)
            messages.warning(request, 'تم تفعيل الجلسة، لكن تعذر الاتصال التلقائي بالشبكة.')

    response = redirect('wifi_entry')
    return set_visit_cookie(response, raw_cookie) if raw_cookie else response


def wifi_entry(request):
    """Render one stable Hub landing for captive Wi-Fi and manual table entry.

    The SSID may be open, but Internet authorization is controlled here. RouterOS
    remains responsible for the physical HotSpot session; this page never accepts a
    router password or trusts a client-supplied MAC address.
    """
    if request.method == 'POST' and request.POST.get('wifi_action') == 'start_guest_wifi':
        return _start_guest_wifi(request)

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

    member_context = resolve_member_from_request(request, touch=False)
    policy = get_guest_wifi_policy()
    guest_error = guest_wifi_policy_error(policy)
    guest_available = bool(
        not guest_error
        and self_service_enabled(get_system_settings())
    )
    active_session = active_browser_session(credential) if credential else None
    active_guest_session = None
    if active_session and hasattr(active_session, 'guest_wifi_grant'):
        active_guest_session = active_session

    response = render(request, 'menu/wifi_entry.html', {
        'table_number_error': table_number_error,
        'table_number_value': raw_number,
        'current_visit': visit,
        'current_table': current_table,
        'current_table_url': current_table_url,
        'came_from_free_access': request.GET.get('free') == '1',
        'member_context': member_context,
        'guest_wifi_available': guest_available,
        'guest_wifi_unavailable_reason': guest_error or '',
        'guest_wifi_code_required': (
            guest_wifi_code_required(policy, member_context) if guest_available else False
        ),
        'guest_wifi_session_minutes': int(policy.session_minutes or 0),
        'guest_wifi_grants_remaining': (
            guest_wifi_grants_remaining(credential, policy) if credential else int(policy.max_sessions_per_day or 0)
        ),
        'active_guest_wifi_session': active_guest_session,
        'active_guest_wifi_network_ready': bool(
            active_guest_session
            and active_guest_session.network_status == InternetSession.NetworkStatus.PROVISIONED
        ) if hasattr(InternetSession, 'NetworkStatus') else bool(
            active_guest_session and active_guest_session.network_status == 'provisioned'
        ),
    })
    # Captive-portal state is device/session specific. Avoid stale intermediary or
    # browser caches showing an old table/account choice on a later connection.
    response['Cache-Control'] = 'no-store, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response
