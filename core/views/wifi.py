"""Customer-facing entry page used by the Hub Wi-Fi captive flow."""

from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse

from core.services.table_visit_access import resolve_table_number
from core.services.visits import resolve_visit_credential


def _validation_message(error):
    return ' '.join(getattr(error, 'messages', [str(error)]))


def wifi_entry(request):
    """Render one stable Hub landing for captive Wi-Fi and manual table entry.

    RouterOS remains responsible for authenticating the physical HotSpot client.
    This endpoint deliberately receives no client MAC address, password, or router
    credential; it only resolves Hub table/account state after the network layer
    has sent the browser here.
    """
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

    response = render(request, 'menu/wifi_entry.html', {
        'table_number_error': table_number_error,
        'table_number_value': raw_number,
        'current_visit': visit,
        'current_table': current_table,
        'current_table_url': current_table_url,
        'came_from_free_access': request.GET.get('free') == '1',
    })
    # Captive-portal state is device/session specific. Avoid stale intermediary or
    # browser caches showing an old table/account choice on a later connection.
    response['Cache-Control'] = 'no-store, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response
