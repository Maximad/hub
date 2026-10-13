"""Internet sessions and Wi‑Fi management views for staff workflows."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import is_owner_or_admin, user_has_capability
from core.models import InternetBandwidthProfile, InternetEntitlement, InternetPackage
from internet.models import StaffInternetDeviceEvidence
from core.services.staff_internet_observation import (
    observation_is_fresh, observed_concurrent_limit_state,
)
from core.services.internet_internal_access import (
    INTERNAL_ORIGINS,
    grant_internal_access,
    internal_grants,
    revoke_internal_access,
)
from core.views_legacy import (
    staff_internet as _legacy_staff_internet,
    staff_internet_cancel,
    staff_internet_end,
    staff_internet_sale as _legacy_staff_internet_sale,
    staff_internet_session,
    staff_internet_start,
    staff_wifi,
)


class InternalAccessGrantForm(forms.Form):
    staff_user = forms.ModelChoiceField(
        label='الموظف',
        queryset=get_user_model().objects.none(),
        empty_label='اختر الموظف',
        widget=forms.Select(attrs={'class': 'hub-input'}),
    )
    grant_kind = forms.ChoiceField(
        label='نوع الوصول',
        choices=(('owner', 'الإدارة'), ('team', 'الفريق')),
        initial='team',
        widget=forms.RadioSelect,
    )
    bandwidth_profile = forms.ModelChoiceField(
        label='ملف الاتصال',
        queryset=InternetBandwidthProfile.objects.none(),
        widget=forms.Select(attrs={'class': 'hub-input'}),
    )
    access_mode = forms.ChoiceField(
        label='نوع الرصيد',
        choices=(
            (InternetPackage.AccessMode.UNLIMITED, 'غير محدود خلال الصلاحية'),
            (InternetPackage.AccessMode.ALLOWANCE, 'رصيد دقائق'),
        ),
        initial=InternetPackage.AccessMode.UNLIMITED,
        widget=forms.Select(attrs={'class': 'hub-input'}),
    )
    validity_value = forms.IntegerField(
        label='مدة الصلاحية', initial=30, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'hub-input', 'inputmode': 'numeric'}),
    )
    validity_unit = forms.ChoiceField(
        label='وحدة الصلاحية',
        choices=InternetPackage.ValidityUnit.choices,
        initial=InternetPackage.ValidityUnit.DAYS,
        widget=forms.Select(attrs={'class': 'hub-input'}),
    )
    session_minutes_limit = forms.IntegerField(
        label='حد الجلسة بالدقائق', required=False, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'hub-input', 'inputmode': 'numeric'}),
    )
    total_minutes_allowed = forms.IntegerField(
        label='إجمالي الدقائق', required=False, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'hub-input', 'inputmode': 'numeric'}),
    )
    daily_minutes_limit = forms.IntegerField(
        label='الحد اليومي بالدقائق', required=False, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'hub-input', 'inputmode': 'numeric'}),
    )
    max_concurrent_devices = forms.IntegerField(
        label='الأجهزة المتزامنة', initial=2, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'hub-input', 'inputmode': 'numeric'}),
    )
    max_registered_devices = forms.IntegerField(
        label='الأجهزة المسجلة', initial=4, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'hub-input', 'inputmode': 'numeric'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        provider_role = getattr(User.Role, 'INTERNET_PROVIDER', 'internet_provider')
        self.fields['staff_user'].queryset = User.objects.filter(is_active=True).exclude(
            role=provider_role,
        ).order_by('first_name', 'last_name', 'username')
        self.fields['bandwidth_profile'].queryset = InternetBandwidthProfile.objects.filter(
            is_active=True,
        ).order_by('name')
        self.fields['bandwidth_profile'].empty_label = None

    def clean(self):
        cleaned = super().clean()
        access_mode = cleaned.get('access_mode')
        total = cleaned.get('total_minutes_allowed')
        concurrent = cleaned.get('max_concurrent_devices')
        registered = cleaned.get('max_registered_devices')
        if access_mode == InternetPackage.AccessMode.ALLOWANCE and not total:
            self.add_error('total_minutes_allowed', 'إجمالي الدقائق مطلوب عند اختيار رصيد دقائق.')
        if access_mode == InternetPackage.AccessMode.UNLIMITED and total:
            self.add_error('total_minutes_allowed', 'اترك إجمالي الدقائق فارغاً للوصول غير المحدود.')
        if concurrent and registered and concurrent > registered:
            self.add_error('max_concurrent_devices', 'لا يمكن أن يتجاوز الحد المتزامن عدد الأجهزة المسجلة.')
        return cleaned


def _internal_access_url():
    return reverse('staff_internet_sale') + '?internal=1'


def _assert_internal_access_manager(user):
    # Both a managerial identity and the dedicated capability are necessary.
    # In particular, an internet-sales permission or a cashier role is insufficient.
    if not (is_owner_or_admin(user) and user_has_capability(user, 'internal_internet_manage')):
        raise PermissionDenied('إدارة إنترنت الفريق مخصصة للإدارة المخوّلة.')


def _internal_access_page(request, form=None):
    grants = list(
        internal_grants().select_related(
            'member', 'internal_staff_grant__user',
        ).order_by('-created_at')[:60]
    )
    recent_device_cutoff = timezone.now() - timedelta(days=30)
    for entitlement in grants:
        entitlement.internal_kind_label = (
            'الإدارة' if entitlement.origin_type == 'internal_owner_grant' else 'الفريق'
        )
        staff_target = getattr(entitlement, 'internal_staff_grant', None)
        entitlement.internal_staff_user = staff_target.user if staff_target else None
        entitlement.internal_subject_label = (
            str(staff_target.user) if staff_target else
            (entitlement.member.name_ar if entitlement.member_id else 'منحة داخلية قديمة')
        )
        entitlement.internal_subject_role = (
            staff_target.user.get_role_display() if staff_target else ''
        )
        snapshot = getattr(staff_target, 'connection_snapshot', None) if staff_target else None
        entitlement.internal_observation = snapshot
        entitlement.internal_observation_current = observation_is_fresh(snapshot)
        entitlement.internal_limit_observation = observed_concurrent_limit_state(entitlement, snapshot)
        entitlement.internal_longest_login_minutes = (
            snapshot.longest_active_uptime_seconds // 60
            if entitlement.internal_observation_current and snapshot.longest_active_uptime_seconds is not None
            else None
        )
        # Counts are shown only when verified recently, never inferred from a
        # login relay request or a stale/failed REST read.
        entitlement.internal_online_count = (
            snapshot.active_login_count if entitlement.internal_observation_current else None
        )
        entitlement.internal_device_count = (
            snapshot.distinct_device_count if entitlement.internal_observation_current else None
        )
        entitlement.internal_observed_devices_30d = (
            StaffInternetDeviceEvidence.objects.filter(
                grant=staff_target, last_seen_at__gte=recent_device_cutoff,
            ).count()
            if staff_target and snapshot and snapshot.last_success_at else None
        )
        entitlement.internal_registered_device_excess_observed = bool(
            entitlement.internal_observed_devices_30d is not None
            and entitlement.max_registered_devices
            and entitlement.internal_observed_devices_30d > entitlement.max_registered_devices
        )
    active_count = sum(
        1 for entitlement in grants
        if entitlement.status in {
            InternetEntitlement.Status.PENDING,
            InternetEntitlement.Status.ACTIVE,
            InternetEntitlement.Status.SUSPENDED,
        }
    )
    return render(request, 'staff/internet_internal_access.html', {
        'internal_access_form': form or InternalAccessGrantForm(),
        'internal_grants': grants,
        'internal_active_count': active_count,
    })


def staff_internet(request):
    return _legacy_staff_internet(request)


@login_required
def staff_internet_sale(request):
    action = request.POST.get('internet_action', '').strip() if request.method == 'POST' else ''
    internal_get = request.method == 'GET' and request.GET.get('internal') == '1'
    internal_post = action in {'internal_grant', 'internal_revoke'}
    if internal_get or internal_post:
        _assert_internal_access_manager(request.user)

    if internal_get:
        return _internal_access_page(request)
    if action == 'internal_grant':
        form = InternalAccessGrantForm(request.POST)
        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
            return _internal_access_page(request, form=form)
        try:
            grant_internal_access(actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return _internal_access_page(request, form=form)
        messages.success(request, 'تم منح الوصول الداخلي للموظف دون إنشاء بيع أو دفعة.')
        return redirect(_internal_access_url())

    if action == 'internal_revoke':
        entitlement = get_object_or_404(
            InternetEntitlement,
            pk=request.POST.get('entitlement_id'),
            origin_type__in=INTERNAL_ORIGINS,
        )
        try:
            revoke_internal_access(entitlement, actor=request.user)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, 'تم إلغاء الوصول الداخلي وإنهاء أي استخدام فعّال عبر دورة الشبكة العادية.')
        return redirect(_internal_access_url())

    return _legacy_staff_internet_sale(request)
