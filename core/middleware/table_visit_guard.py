"""Require an explicit table visit before starting customer Internet."""
from django.shortcuts import redirect
from django.urls import reverse

from core.models import TableArea
from core.services.visits import resolve_visit_credential


class TableVisitGuardMiddleware:
    """Block legacy direct-start requests from silently joining another bill.

    The customer must first create a separate visit or join an existing visit with
    its PIN. Normal requests already carrying the correct visit credential pass
    through unchanged.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path == reverse('visit_internet_start'):
            raw_table = request.POST.get('table')
            if raw_table:
                table = TableArea.objects.filter(qr_token=raw_table).first()
                if table is not None:
                    credential = resolve_visit_credential(request, touch=False)
                    if not credential or credential.visit.table_id != table.pk:
                        target = reverse('menu_table', kwargs={'qr_token': table.qr_token})
                        return redirect(target + '?choose=1')
        return self.get_response(request)
