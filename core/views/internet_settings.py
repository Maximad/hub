from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import require_staff_capability
from core.models import (
    ActivityLog,
    InternetBandwidthProfile,
    InternetNetworkOperation,
    InternetPackage,
    InternetPartner,
    InternetSession,
)
from core.services.internet_operations import (
    requeue_failed_network_operation,
    run_readonly_mikrotik_healthcheck,
)
from core.services.internet_readiness import (
    internet_readiness_report,
    mikrotik_enablement_preflight,
    worker_is_fresh,
)
from internet.models import InternetSessionNetworkOperation, WifiNetwork


class PartnerForm(forms.ModelForm):
    class Meta:
        model = InternetPartner
        fields = ('name', 'revenue_share_percent', 'active', 'is_default')


class ProfileForm(forms.ModelForm):
    class Meta:
        model = InternetBandwidthProfile
        fields = ('code', 'name', 'download_limit_kbps', 'upload_limit_kbps', 'router_profile_name', 'is_active')


def _handle_operations_action(request):
    action = request.POST.get('operation_action', '').strip()
    if action == 'mikrotik_healthcheck':
        ok, message = run_readonly_mikrotik_healthcheck(actor=request.user)
        (messages.success if ok else messages.error)(request, message)
        return True
    if action == 'retry_network_operation':
        try:
            operation_id = int(request.POST.get('operation_id', ''))
            requeue_failed_network_operation(
                kind=request.POST.get('operation_kind', '').strip(),
                operation_id=operation_id,
                actor=request.user,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                text = next(iter(exc.messages), 'تعذر إعادة محاولة عملية الشبكة.')
            else:
                text = 'معرّف عملية الشبكة غير صالح.'
            messages.error(request, text)
        else:
            messages.success(request, 'أعيدت العملية إلى قائمة الانتظار. سيلتقطها عامل الإنترنت تلقائياً.')
        return True
    return False


@require_staff_capability('settings')
def internet_settings(request):
    if request.method == 'POST':
        if _handle_operations_action(request):
            return redirect('staff_internet_settings')
        messages.error(request, 'إجراء التشغيل غير معروف.')
        return redirect('staff_internet_settings')

    packages = InternetPackage.objects.filter(is_active=True)
    readiness = internet_readiness_report()
    preflight = mikrotik_enablement_preflight()
    state = preflight['state']

    entitlement_counts = {
        status: InternetNetworkOperation.objects.filter(status=status).count()
        for status, _ in InternetNetworkOperation.Status.choices
    }
    session_counts = {
        status: InternetSessionNetworkOperation.objects.filter(status=status).count()
        for status, _ in InternetSessionNetworkOperation.Status.choices
    }
    entitlement_operations = list(
        InternetNetworkOperation.objects.select_related(
            'entitlement', 'entitlement__member', 'entitlement__package',
        ).order_by('-updated_at')[:30]
    )
    session_operations = list(
        InternetSessionNetworkOperation.objects.select_related(
            'session', 'session__member', 'session__visit',
        ).order_by('-updated_at')[:30]
    )

    active_session_rows = []
    sessions = (
        InternetSession.objects.filter(status=InternetSession.Status.ACTIVE)
        .select_related('member', 'visit', 'network_state')
        .order_by('-start_time', '-pk')[:30]
    )
    for session in sessions:
        try:
            network_state = session.network_state
        except ObjectDoesNotExist:
            network_state = None
        active_session_rows.append({'session': session, 'network_state': network_state})

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
        'pending_network_operations': entitlement_counts.get('pending', 0) + entitlement_counts.get('processing', 0),
        'failed_network_operations': entitlement_counts.get('failed', 0),
        'last_network_operation': entitlement_operations[0] if entitlement_operations else None,
        'partner_form': PartnerForm(),
        'profile_form': ProfileForm(),
        'readiness': readiness,
        'preflight': preflight,
        'operations_state': state,
        'worker_fresh': worker_is_fresh(state),
        'entitlement_counts': entitlement_counts,
        'session_counts': session_counts,
        'entitlement_operations': entitlement_operations,
        'session_operations': session_operations,
        'active_session_rows': active_session_rows,
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
