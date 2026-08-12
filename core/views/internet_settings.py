from django import forms
from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from accounts.permissions import require_staff_capability
from core.models import (ActivityLog, InternetBandwidthProfile, InternetNetworkOperation,
                         InternetPackage, InternetPartner)
from internet.models import WifiNetwork


class PartnerForm(forms.ModelForm):
    class Meta:
        model = InternetPartner
        fields = ('name', 'revenue_share_percent', 'active', 'is_default')


class ProfileForm(forms.ModelForm):
    class Meta:
        model = InternetBandwidthProfile
        fields = ('code', 'name', 'download_limit_kbps', 'upload_limit_kbps', 'router_profile_name', 'is_active')


@require_staff_capability('settings')
def internet_settings(request):
    packages = InternetPackage.objects.filter(is_active=True)
    context = {
        'partners': InternetPartner.objects.order_by('-is_default', 'name'),
        'profiles': InternetBandwidthProfile.objects.order_by('name'),
        'networks': WifiNetwork.objects.select_related('bandwidth_profile').order_by('name_ar'),
        'default_partner': InternetPartner.objects.filter(active=True, is_default=True).first(),
        'inherited_packages': packages.filter(partner__isnull=True).count(),
        'partner_overrides': packages.filter(partner__isnull=False).count(),
        'percent_overrides': packages.filter(partner_share_percent__isnull=False).count(),
        'mikrotik_enabled': settings.MIKROTIK_ENABLED,
        'mikrotik_configured': bool(settings.MIKROTIK_BASE_URL and settings.MIKROTIK_HOTSPOT_SERVER),
        'network_backends': WifiNetwork.objects.values_list('network_backend', flat=True).distinct(),
        'pending_network_operations': InternetNetworkOperation.objects.filter(
            status__in=('pending', 'processing')).count(),
        'failed_network_operations': InternetNetworkOperation.objects.filter(status='failed').count(),
        'last_network_operation': InternetNetworkOperation.objects.order_by('-updated_at').first(),
        'partner_form': PartnerForm(), 'profile_form': ProfileForm(),
    }
    return render(request, 'staff/internet_settings.html', context)


@require_POST
@require_staff_capability('settings')
def internet_partner_save(request, partner_id=None):
    partner = get_object_or_404(InternetPartner, pk=partner_id) if partner_id else InternetPartner()
    before = {field: getattr(partner, field, None) for field in ('name', 'active', 'is_default', 'revenue_share_percent')}
    form = PartnerForm(request.POST, instance=partner)
    if form.is_valid():
        partner = form.save()
        changed = [field for field in form.changed_data if before.get(field) != getattr(partner, field)]
        ActivityLog.objects.create(actor=request.user, action='internet.partner_changed', details={'partner_id': partner.pk, 'fields_changed': changed})
        messages.success(request, 'تم حفظ شريك الإنترنت. التغييرات تطبق على السجلات الجديدة فقط.')
    else:
        messages.error(request, '; '.join(sum(form.errors.values(), [])))
    return redirect('staff_internet_settings')


@require_POST
@require_staff_capability('settings')
def internet_profile_save(request, profile_id=None):
    profile = get_object_or_404(InternetBandwidthProfile, pk=profile_id) if profile_id else InternetBandwidthProfile()
    form = ProfileForm(request.POST, instance=profile)
    if form.is_valid():
        profile = form.save()
        ActivityLog.objects.create(actor=request.user, action='internet.bandwidth_profile_changed', details={'profile_id': profile.pk, 'fields_changed': form.changed_data})
        messages.success(request, 'تم حفظ ملف السرعة.')
    else:
        messages.error(request, '; '.join(sum(form.errors.values(), [])))
    return redirect('staff_internet_settings')
