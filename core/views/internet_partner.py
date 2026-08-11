from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.shortcuts import render
from django.utils import timezone
from core.models import (InternetEntitlement, InternetPartnerUser, InternetRevenueShare,
                         InternetRevenueShareAdjustment, Payment)


MAX_REPORTING_DAYS = 366


class PartnerReportingDateRangeForm(forms.Form):
    start = forms.DateField()
    end = forms.DateField()

    def clean(self):
        cleaned_data = super().clean()
        start, end = cleaned_data.get('start'), cleaned_data.get('end')
        if start and end:
            if start > end:
                raise forms.ValidationError('يجب أن يكون تاريخ البداية قبل تاريخ النهاية.')
            if (end - start).days + 1 > MAX_REPORTING_DAYS:
                raise forms.ValidationError(
                    f'يجب ألا تتجاوز فترة التقرير {MAX_REPORTING_DAYS} يوماً.')
        return cleaned_data


@login_required
def internet_partner_dashboard(request):
    today = timezone.localdate()
    date_range_form = PartnerReportingDateRangeForm({
        'start': request.GET.get('start') or today,
        'end': request.GET.get('end') or today,
    })
    if not date_range_form.is_valid():
        return render(request, 'partner/internet_dashboard.html', {
            'date_range_form': date_range_form,
            'start': request.GET.get('start', ''),
            'end': request.GET.get('end', ''),
        }, status=400)

    start = date_range_form.cleaned_data['start']
    end = date_range_form.cleaned_data['end']
    association = InternetPartnerUser.objects.select_related('partner').filter(user=request.user, partner__active=True).first()
    if association is None:
        raise PermissionDenied('هذه الصفحة مخصصة لمستخدمي شريك الإنترنت.')
    entitlements = InternetEntitlement.objects.filter(partner=association.partner).select_related(
        'member', 'member__default_plan', 'package', 'payment', 'revenue_share').prefetch_related('devices', 'sessions').order_by('-created_at')
    adjustment_range = InternetRevenueShareAdjustment.objects.filter(
        business_date__range=(start, end))
    shares = InternetRevenueShare.objects.filter(
        partner=association.partner, business_date__range=(start, end)
    ).select_related('payment', 'entitlement').prefetch_related(
        Prefetch('adjustments', queryset=adjustment_range))
    realized = [x for x in shares if x.payment_id and x.payment.method not in {Payment.Method.UNPAID, Payment.Method.FREE, Payment.Method.MEMBER_DISCOUNT}]
    adjustments = [a for x in shares for a in x.adjustments.all()]
    # Safe reporting fallback while reversal/refund write hooks remain outside
    # protected finance internals: never leave a reversed/cancelled sale payable.
    adjusted_share_ids = {a.revenue_share_id for a in adjustments if a.kind in {'reversal', 'refund'}}
    virtually_reversed = [x for x in realized if x.pk not in adjusted_share_ids and
        (x.payment.is_reversed or x.entitlement.status == InternetEntitlement.Status.CANCELLED)]
    totals = {
        'gross': sum((x.gross_amount_syp for x in realized), 0) - sum((x.gross_amount_syp for x in virtually_reversed), 0) + sum((a.gross_delta_syp for a in adjustments), 0),
        'partner': sum((x.partner_amount_syp for x in realized), 0) - sum((x.partner_amount_syp for x in virtually_reversed), 0) + sum((a.partner_delta_syp for a in adjustments), 0),
        'hub': sum((x.hub_amount_syp for x in realized), 0) - sum((x.hub_amount_syp for x in virtually_reversed), 0) + sum((a.hub_delta_syp for a in adjustments), 0),
    }
    return render(request, 'partner/internet_dashboard.html', {'association': association, 'entitlements': entitlements, 'shares': shares, 'totals': totals, 'start': start, 'end': end, 'date_range_form': date_range_form})
