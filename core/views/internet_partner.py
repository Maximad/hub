from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone
from core.models import InternetEntitlement, InternetPartnerUser, InternetRevenueShare

@login_required
def internet_partner_dashboard(request):
    association = InternetPartnerUser.objects.select_related('partner').filter(user=request.user, partner__active=True).first()
    if association is None:
        raise PermissionDenied('هذه الصفحة مخصصة لمستخدمي شريك الإنترنت.')
    start = request.GET.get('start') or str(timezone.localdate())
    end = request.GET.get('end') or str(timezone.localdate())
    entitlements = InternetEntitlement.objects.filter(partner=association.partner).select_related('member', 'package', 'payment').prefetch_related('devices').order_by('-created_at')
    shares = InternetRevenueShare.objects.filter(partner=association.partner, business_date__range=(start, end))
    totals = {'gross': sum((x.gross_amount_syp for x in shares), 0), 'partner': sum((x.partner_amount_syp for x in shares), 0), 'hub': sum((x.hub_amount_syp for x in shares), 0)}
    return render(request, 'partner/internet_dashboard.html', {'association': association, 'entitlements': entitlements, 'shares': shares, 'totals': totals, 'start': start, 'end': end})
