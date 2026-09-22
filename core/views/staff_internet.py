"""Internet sessions and Wi‑Fi management views for staff workflows."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.models import InternetBandwidthProfile, InternetEntitlement, InternetPackage
from core.services.internet_internal_access import (
    INTERNAL_ORIGINS,
    grant_internal_access,
    internal_grants,
    revoke_internal_access,
)
from core.views_legacy import (
    _assert_staff_capability,
    staff_internet as _legacy_staff_internet,
    staff_internet_cancel,
    staff_internet_end,
    staff_internet_sale as _legacy_staff_internet_sale,
    staff_internet_session,
    staff_internet_start,
    staff_wifi,
)


class StaffUserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        name = obj.get_full_name() or obj.username
        return f'{name} — {obj.get_role_display()} — {obj.phone}'


class InternalAccessGrantForm(forms.Form):
    staff_user = StaffUserChoiceField(
        label='الموظف',
        queryset=get_user_model().objects.none(),
        widget=forms.Select(attrs={'class': 'hub-input'}),
    )
    grant_kind = forms.ChoiceField(
        label='نوع الوصول',
        choices=(('owner', 'الإدارة'), ('team', 'الفريق')),
        widget=forms.Select(attrs={'class': 'hub-input'}),
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


def _internal_access_page(request, form=None):
    grants = list(
        internal_grants().select_related('member').order_by('-created_at')[:60]
    )
    User = get_user_model()
    staff_by_phone = {
        user.phone: user
        for user in User.objects.filter(
            is_active=True,
            phone__in=[g.guest_phone for g in grants if g.guest_phone and not g.member_id],
        )
    }
    for entitlement in grants:
        entitlement.internal_kind_label = (
            'الإدارة' if entitlement.origin_type == 'internal_owner_grant' else 'الفريق'
        )
        entitlement.staff_user = (
            staff_by_phone.get(entitlement.guest_phone) if not entitlement.member_id else None
        )
        entitlement.internal_identity_name = (
            str(entitlement.staff_user)
            if entitlement.staff_user
            else (entitlement.member.name_ar if entitlement.member_id else entitlement.guest_name or 'حساب موظف سابق')
        )
        entitlement.internal_identity_phone = (
            entitlement.staff_user.phone if entitlement.staff_user else (
                entitlement.member.phone if entitlement.member_id else entitlement.guest_phone
            )
        )
        entitlement.internal_identity_role = (
            entitlement.staff_user.get_role_display() if entitlement.staff_user else (
                'منحة قديمة مرتبطة بعضو' if entitlement.member_id else '—'
            )
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
    _assert_staff_capability(request.user, 'members/internet')

    if request.method == 'GET' and request.GET.get('internal') == '1':
        return _internal_access_page(request)

    action = request.POST.get('internet_action', '').strip() if request.method == 'POST' else ''
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
        messages.success(request, 'تم منح الوصول الداخلي لحساب الموظف دون إنشاء بيع أو دفعة.')
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
