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
from core.services.mikrotik_portal_policy import (
    desired_portal_policy,
    sync_portal_policy,
)
from core.services.internet_readiness import (
    internet_readiness_report,
    mikrotik_enablement_preflight,
    worker_is_fresh,
)
from internet.guest_wifi import current_venue_code
from internet.models import GuestWifiPolicy, InternetSessionNetworkOperation, WifiNetwork


class PartnerForm(forms.ModelForm):
    class Meta:
        model = InternetPartner
        fields = ('name', 'revenue_share_percent', 'active', 'is_default')
        labels = {
            'name': 'اسم المزوّد',
            'revenue_share_percent': 'نسبة حصة المزوّد',
            'active': 'مزوّد فعّال',
            'is_default': 'المزوّد الافتراضي',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'hub-input')


class ProfileForm(forms.ModelForm):
    class Meta:
        model = InternetBandwidthProfile
        fields = ('code', 'name', 'download_limit_kbps', 'upload_limit_kbps', 'router_profile_name', 'is_active')
        labels = {
            'code': 'الرمز الداخلي',
            'name': 'اسم ملف السرعة',
            'download_limit_kbps': 'سرعة التنزيل (Kbps)',
            'upload_limit_kbps': 'سرعة الرفع (Kbps)',
            'router_profile_name': 'اسم الملف في RouterOS',
            'is_active': 'ملف فعّال',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'hub-input')


def _speed_label(kbps):
    if not kbps:
        return 'غير محددة'
    if kbps >= 1000 and kbps % 1000 == 0:
        return f'{kbps // 1000} Mbps'
    return f'{kbps} Kbps'


class GuestWifiPolicyForm(forms.ModelForm):
    """Deliberately compact controls for the slow/basic Internet business policy."""

    class Meta:
        model = GuestWifiPolicy
        fields = (
            'enabled',
            'bandwidth_profile',
            'session_minutes',
            'daily_complimentary_minutes',
            'order_bonus_enabled',
            'order_bonus_minutes',
            'qualifying_order_minimum_syp',
            'require_venue_code',
            'code_rotation_minutes',
            'member_bypass_venue_code',
        )
        widgets = {
            'bandwidth_profile': forms.Select(attrs={'class': 'hub-input'}),
            'session_minutes': forms.NumberInput(attrs={'class': 'hub-input', 'min': 1, 'inputmode': 'numeric'}),
            'daily_complimentary_minutes': forms.NumberInput(attrs={'class': 'hub-input', 'min': 1, 'inputmode': 'numeric'}),
            'order_bonus_minutes': forms.NumberInput(attrs={'class': 'hub-input', 'min': 0, 'inputmode': 'numeric'}),
            'qualifying_order_minimum_syp': forms.NumberInput(attrs={'class': 'hub-input', 'min': 0, 'inputmode': 'numeric'}),
            'code_rotation_minutes': forms.NumberInput(attrs={'class': 'hub-input', 'min': 1, 'inputmode': 'numeric'}),
        }

    def clean(self):
        cleaned = super().clean()
        initial = int(cleaned.get('session_minutes') or 0)
        cap = int(cleaned.get('daily_complimentary_minutes') or 0)
        if initial and cap and cap < initial:
            self.add_error('daily_complimentary_minutes', 'السقف اليومي يجب أن يساوي أو يتجاوز الرصيد الأولي.')
        if cleaned.get('order_bonus_enabled') and int(cleaned.get('order_bonus_minutes') or 0) <= 0:
            self.add_error('order_bonus_minutes', 'حدد دقائق مكافأة أكبر من صفر أو عطّل مكافأة الطلب.')
        if cleaned.get('require_venue_code') and int(cleaned.get('code_rotation_minutes') or 0) <= 0:
            self.add_error('code_rotation_minutes', 'حدد مدة صالحة لتغيير رمز المكان.')
        return cleaned


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
    if action == 'sync_mikrotik_portal_policy':
        try:
            report = sync_portal_policy()
        except Exception as exc:
            message = (
                next(iter(exc.messages), 'تعذر مزامنة سياسة بوابة الشبكة.')
                if isinstance(exc, ValidationError)
                else 'تعذر مزامنة سياسة بوابة الشبكة. راجع اتصال MikroTik وصلاحيات حساب الخدمة.'
            )
            ActivityLog.objects.create(
                actor=request.user,
                action='internet.mikrotik_portal_policy_sync_failed',
                details={'error_type': type(exc).__name__},
            )
            messages.error(request, message)
        else:
            ActivityLog.objects.create(
                actor=request.user,
                action='internet.mikrotik_portal_policy_synced',
                details={
                    'ready': report['ready'],
                    'changes': report['changes'],
                    'checks': [
                        {'code': item['code'], 'ok': item['ok']}
                        for item in report['checks']
                    ],
                },
            )
            if report['ready']:
                changed = len(report['changes'])
                messages.success(
                    request,
                    f'بوابة الشبكة جاهزة. أصلح هَبّ {changed} إعداداً.'
                    if changed else 'بوابة الشبكة جاهزة ولا تحتاج إلى تغييرات.',
                )
            else:
                messages.warning(request, 'اكتملت المزامنة لكن بقي فحص غير ناجح؛ راجع النتيجة أدناه.')
        return True
    return False


def _save_guest_wifi_policy(request, policy):
    form = GuestWifiPolicyForm(request.POST, instance=policy)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return False
    before = {
        name: getattr(policy, name)
        for name in form.fields
    }
    policy = form.save()
    changed = [
        name for name in form.changed_data
        if before.get(name) != getattr(policy, name)
    ]
    ActivityLog.objects.create(
        actor=request.user,
        action='internet.basic_wifi_policy_changed',
        details={'policy_id': policy.pk, 'fields_changed': changed},
    )
    messages.success(request, 'تم حفظ سياسة الإنترنت الأساسي. تطبّق القواعد الجديدة على المنح والجلسات الجديدة.')
    return True


@require_staff_capability('settings')
def internet_settings(request):
    guest_wifi_policy, _ = GuestWifiPolicy.objects.get_or_create(key='default')
    if request.method == 'POST':
        if request.POST.get('settings_action') == 'save_basic_wifi_policy':
            _save_guest_wifi_policy(request, guest_wifi_policy)
            return redirect('staff_internet_settings')
        if _handle_operations_action(request):
            return redirect('staff_internet_settings')
        messages.error(request, 'إجراء التشغيل غير معروف.')
        return redirect('staff_internet_settings')

    packages = InternetPackage.objects.filter(is_active=True)
    readiness = internet_readiness_report()
    preflight = mikrotik_enablement_preflight()
    state = preflight['state']
    try:
        portal_policy = desired_portal_policy()
        portal_policy_error = ''
    except ValidationError as exc:
        portal_policy = None
        portal_policy_error = next(iter(exc.messages), 'إعداد سياسة البوابة غير مكتمل.')
    last_portal_policy_sync = (
        ActivityLog.objects.filter(
            action__in=(
                'internet.mikrotik_portal_policy_synced',
                'internet.mikrotik_portal_policy_sync_failed',
            ),
        )
        .order_by('-created_at', '-pk')
        .first()
    )

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

    profiles = list(InternetBandwidthProfile.objects.order_by('name'))
    for profile in profiles:
        profile.download_display = _speed_label(profile.download_limit_kbps)
        profile.upload_display = _speed_label(profile.upload_limit_kbps)
    failed_total = entitlement_counts.get('failed', 0) + session_counts.get('failed', 0)
    pending_total = (
        entitlement_counts.get('pending', 0)
        + entitlement_counts.get('processing', 0)
        + session_counts.get('pending', 0)
        + session_counts.get('processing', 0)
    )

    context = {
        'partners': InternetPartner.objects.order_by('-is_default', 'name'),
        'profiles': profiles,
        'networks': WifiNetwork.objects.select_related('bandwidth_profile').order_by('name_ar'),
        'default_partner': InternetPartner.objects.filter(active=True, is_default=True).first(),
        'inherited_packages': packages.filter(partner__isnull=True).count(),
        'partner_overrides': packages.filter(partner__isnull=False).count(),
        'percent_overrides': packages.filter(partner_share_percent__isnull=False).count(),
        'mikrotik_enabled': settings.MIKROTIK_ENABLED,
        'mikrotik_configured': bool(settings.MIKROTIK_BASE_URL and settings.MIKROTIK_HOTSPOT_SERVER),
        'network_backends': WifiNetwork.objects.values_list('network_backend', flat=True).distinct(),
        'pending_network_operations': pending_total,
        'failed_network_operations': failed_total,
        'last_network_operation': entitlement_operations[0] if entitlement_operations else None,
        'partner_form': PartnerForm(),
        'profile_form': ProfileForm(),
        'guest_wifi_policy': guest_wifi_policy,
        'guest_wifi_form': GuestWifiPolicyForm(instance=guest_wifi_policy),
        'guest_wifi_current_code': (
            current_venue_code(guest_wifi_policy)
            if guest_wifi_policy.enabled and guest_wifi_policy.require_venue_code
            else ''
        ),
        'readiness': readiness,
        'preflight': preflight,
        'operations_state': state,
        'worker_fresh': worker_is_fresh(state),
        'portal_policy': portal_policy,
        'portal_policy_error': portal_policy_error,
        'last_portal_policy_sync': last_portal_policy_sync,
        'entitlement_counts': entitlement_counts,
        'session_counts': session_counts,
        'entitlement_operations': entitlement_operations,
        'session_operations': session_operations,
        'active_session_rows': active_session_rows,
        'advanced_diagnostics_open': bool(
            failed_total or not worker_is_fresh(state) or readiness.get('status') == 'FAIL'
        ),
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
