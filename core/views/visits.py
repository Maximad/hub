from django.shortcuts import redirect, render
from django.urls import reverse

from core.services.visits import resolve_visit_credential
from core.settings_helpers import get_system_settings


def current_visit(request):
    if not get_system_settings().customer_visits_enabled:
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        return redirect('menu_public')
    visit = credential.visit
    orders = visit.orders.exclude(status='cancelled').prefetch_related('items', 'discounts', 'payments').order_by('created_at')
    menu_url = reverse('menu_table', kwargs={'qr_token': visit.table.qr_token}) if visit.table_id else reverse('menu_public')
    return render(request, 'menu/current_visit.html', {'visit': visit, 'orders': orders, 'menu_url': menu_url})
