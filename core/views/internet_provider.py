"""Partner-scoped Internet provider workspace.

The provider role is an external operational role. Every queryset is anchored to
InternetPartnerUser before data is loaded; staff/POS/finance objects are never
used as an implicit authorization boundary.
"""
import csv
from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import (
    CAPABILITY_LABELS,
    get_staff_capabilities,
    is_owner_or_admin,
    user_has_capability,
)
from core.models import (
    ActivityLog,
    InternetBandwidthProfile,
    InternetEntitlement,
    InternetNetworkOperation,
    InternetPackage,
    InternetPartnerUser,
    InternetRevenueShare,
    InternetRevenueShareAdjustment,
    InternetSession,
    InternetUsageLedger,
    Member,
    Payment,
)
from core.services.internet_access import daily_minutes_remaining, daily_minutes_used, end_usage_session
from core.services.internet_lifecycle import resume_internet_entitlement, suspend_internet_entitlement
from core.services.internet_operations import run_readonly_mikrotik_healthcheck
from core.services.internet_readiness import internet_readiness_report, mikrotik_enablement_preflight, worker_is_fresh
from internet.models import GuestWifiPolicy, InternetOperationsState, WifiNetwork
from members.models import MembershipSubscription


MAX_REPORTING_DAYS = 366
PROVIDER_CAPABILITIES = (
    'provider_dashboard',
    'internet_view_members',
    'internet_manage_members',
    'internet_view_sessions',
    'internet_manage_sessions',
    'internet_view_subscriptions',
    'internet_manage_subscriptions',
    'internet_view_packages',
    'internet_manage_packages',
    'internet_view_network',
    'internet_manage_network',
    'internet_view_reports',
    'internet_manage_provider_settings',
)


class ProviderAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.role != User.Role.INTERNET_PROVIDER:
            raise forms.ValidationError('هذا الحساب غير مخصص لبوابة مزوّد الإنترنت.', code='invalid_role')
        if not InternetPartnerUser.objects.filter(user=user, partner__active=True).exists():
            raise forms.ValidationError('الحساب غير مرتبط بمزوّد إنترنت فعّال.', code='missing_partner')


class ProviderReportingDateRangeForm(forms.Form):
    start = forms.DateField(label='من', widget=forms.DateInput(attrs={'type': 'date', 'class': 'hub-input'}))
    end = forms.DateField(label='إلى', widget=forms.DateInput(attrs={'type': 'date', 'class': 'hub-input'}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start'), cleaned.get('end')
        if start and end:
            if start > end:
                raise forms.ValidationError('يجب أن يكون تاريخ البداية قبل تاريخ النهاية.')
            if (end - start).days + 1 > MAX_REPORTING_DAYS:
                raise forms.ValidationError(f'يجب ألا تتجاوز فترة التقرير {MAX_REPORTING_DAYS} يوماً.')
        return cleaned


class ProviderPackageForm(forms.ModelForm):
    class Meta:
        model = InternetPackage
        fields = (
            'name_ar', 'name_en', 'description_ar', 'description_en', 'code',
            'price_syp', 'access_mode', 'activation_policy', 'validity_value',
            'validity_unit', 'session_minutes_limit', 'total_minutes_limit',
            'daily_minutes_limit', 'bandwidth_profile', 'max_concurrent_devices',
            'max_registered_devices', 'member_only', 'guest_allowed', 'is_active',
            'visible_to_staff', 'visible_to_customer', 'sort_order', 'notes',
        )
        labels = {
            'name_ar': 'اسم الباقة بالعربية', 'name_en': 'الاسم بالإنجليزية',
            'description_ar': 'الوصف بالعربية', 'description_en': 'الوصف بالإنجليزية',
            'code': 'رمز الباقة', 'price_syp': 'السعر بالليرة الجديدة',
            'access_mode': 'نمط الوصول', 'activation_policy': 'سياسة التفعيل',
            'validity_value': 'مدة الصلاحية', 'validity_unit': 'وحدة الصلاحية',
            'session_minutes_limit': 'حد الجلسة بالدقائق',
            'total_minutes_limit': 'إجمالي الدقائق', 'daily_minutes_limit': 'الحد اليومي',
            'bandwidth_profile': 'ملف السرعة', 'max_concurrent_devices': 'الأجهزة المتزامنة',
            'max_registered_devices': 'الأجهزة المسجلة', 'member_only': 'للأعضاء فقط',
            'guest_allowed': 'السماح للزوار', 'is_active': 'فعّالة',
            'visible_to_staff': 'ظاهرة لفريق هَبّ', 'visible_to_customer': 'ظاهرة للعميل',
            'sort_order': 'ترتيب العرض', 'notes': 'ملاحظات',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'hub-input')


def _active_association(user):
    if not user or not user.is_authenticated:
        return None
    if user.role != User.Role.INTERNET_PROVIDER and not is_owner_or_admin(user):
        return None
    return (
        InternetPartnerUser.objects.select_related('partner')
        .filter(user=user, partner__active=True)
        .order_by('pk')
        .first()
    )


def provider_capability_required(capability):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                login_url = reverse('internet_provider_login')
                return redirect(f'{login_url}?next={request.get_full_path()}')
            association = _active_association(request.user)
            if association is None or not user_has_capability(request.user, capability):
                raise PermissionDenied('لا تملك صلاحية الوصول إلى بوابة مزوّد الإنترنت.')
            request.internet_partner_association = association
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _provider_packages(partner):
    scope = Q(partner=partner)
    if partner.is_default:
        scope |= Q(partner__isnull=True)
    return InternetPackage.objects.filter(scope)


def _provider_entitlements(partner):
    return InternetEntitlement.objects.filter(partner=partner)


def _provider_sessions(partner):
    # Package-less sessions do not yet carry partner provenance and therefore
    # fail closed instead of being guessed into an external provider's scope.
    return InternetSession.objects.filter(entitlement__partner=partner)


def _date_range_form(request, *, default_days=0):
    today = timezone.localdate()
    values = {
        'start': request.GET.get('start') or today - timedelta(days=default_days),
        'end': request.GET.get('end') or today,
    }
    return ProviderReportingDateRangeForm(values)


def _revenue_report(partner, start, end):
    adjustment_range = InternetRevenueShareAdjustment.objects.filter(business_date__range=(start, end))
    shares = (
        InternetRevenueShare.objects.filter(partner=partner, business_date__range=(start, end))
        .select_related('payment', 'entitlement', 'entitlement__member', 'package')
        .prefetch_related(Prefetch('adjustments', queryset=adjustment_range))
        .order_by('-business_date', '-created_at')
    )
    realized = [
        share for share in shares
        if share.payment_id and share.payment.method not in {
            Payment.Method.UNPAID, Payment.Method.FREE, Payment.Method.MEMBER_DISCOUNT,
        }
    ]
    adjustments = [adjustment for share in shares for adjustment in share.adjustments.all()]
    adjusted_share_ids = {
        adjustment.revenue_share_id
        for adjustment in adjustments
        if adjustment.kind in {'reversal', 'refund'}
    }
    virtually_reversed = [
        share for share in realized
        if share.pk not in adjusted_share_ids
        and (share.payment.is_reversed or share.entitlement.status == InternetEntitlement.Status.CANCELLED)
    ]
    zero = Decimal('0')
    totals = {
        'gross': sum((share.gross_amount_syp for share in realized), zero)
                 - sum((share.gross_amount_syp for share in virtually_reversed), zero)
                 + sum((adjustment.gross_delta_syp for adjustment in adjustments), zero),
        'partner': sum((share.partner_amount_syp for share in realized), zero)
                   - sum((share.partner_amount_syp for share in virtually_reversed), zero)
                   + sum((adjustment.partner_delta_syp for adjustment in adjustments), zero),
        'hub': sum((share.hub_amount_syp for share in realized), zero)
               - sum((share.hub_amount_syp for share in virtually_reversed), zero)
               + sum((adjustment.hub_delta_syp for adjustment in adjustments), zero),
    }
    return shares, totals


def _base_context(request, **extra):
    association = request.internet_partner_association
    capabilities = get_staff_capabilities(request.user)
    context = {
        'association': association,
        'provider_partner': association.partner,
        'provider_caps': capabilities,
    }
    context.update(extra)
    return context


def internet_provider_login(request):
    association = _active_association(request.user)
    if association and user_has_capability(request.user, 'provider_dashboard'):
        return redirect('internet_provider_dashboard')
    form = ProviderAuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        auth_login(request, form.get_user())
        destination = request.POST.get('next', '').strip()
        if not destination or not url_has_allowed_host_and_scheme(
            destination, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            destination = reverse('internet_provider_dashboard')
        return redirect(destination)
    return render(request, 'provider/login.html', {'form': form, 'next': request.GET.get('next', '')})


@require_POST
def internet_provider_logout(request):
    auth_logout(request)
    return redirect('internet_provider_login')


@provider_capability_required('provider_dashboard')
def internet_provider_dashboard(request):
    partner = request.internet_partner_association.partner
    now = timezone.now()
    today = timezone.localdate()
    entitlements = _provider_entitlements(partner)
    active_entitlements = entitlements.filter(status=InternetEntitlement.Status.ACTIVE).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=now)
    )
    sessions = _provider_sessions(partner)
    _, revenue_today = _revenue_report(partner, today, today)
    operations_state = InternetOperationsState.objects.filter(key='default').first()
    metrics = {
        'active_sessions': sessions.filter(status=InternetSession.Status.ACTIVE).count(),
        'active_subscriptions': active_entitlements.count(),
        'customers': entitlements.exclude(member__isnull=True).values('member_id').distinct().count(),
        'expiring_soon': active_entitlements.filter(
            valid_until__gt=now, valid_until__lte=now + timedelta(days=3),
        ).count(),
        'minutes_today': InternetUsageLedger.objects.filter(
            entitlement__partner=partner, business_date=today,
        ).aggregate(total=Sum('minutes'))['total'] or 0,
        'network_errors': entitlements.filter(
            network_status=InternetEntitlement.NetworkStatus.PROVISION_ERROR,
        ).count(),
    }
    recent_sessions = sessions.select_related('member', 'package', 'entitlement').order_by('-start_time')[:8]
    expiring = active_entitlements.select_related('member', 'package').order_by('valid_until')[:8]
    recent_entitlements = entitlements.select_related('member', 'package').order_by('-created_at')[:8]
    return render(request, 'provider/dashboard.html', _base_context(
        request,
        metrics=metrics,
        revenue_today=revenue_today,
        recent_sessions=recent_sessions,
        recent_entitlements=recent_entitlements,
        expiring_entitlements=expiring,
        operations_state=operations_state,
        worker_fresh=worker_is_fresh(operations_state),
    ))


@provider_capability_required('internet_view_members')
def internet_provider_members(request):
    partner = request.internet_partner_association.partner
    query = request.GET.get('q', '').strip()
    entitlement_prefetch = Prefetch(
        'internet_entitlements',
        queryset=_provider_entitlements(partner).select_related('package').order_by('-created_at'),
        to_attr='provider_entitlements',
    )
    members = Member.objects.filter(internet_entitlements__partner=partner).distinct()
    if query:
        search = Q(name_ar__icontains=query) | Q(name_en__icontains=query)
        if request.internet_partner_association.can_view_customer_phone:
            search |= Q(phone__icontains=query)
        members = members.filter(search)
    members = members.prefetch_related(entitlement_prefetch).order_by('name_ar')
    return render(request, 'provider/members.html', _base_context(
        request, members=members, query=query,
    ))


@provider_capability_required('internet_view_members')
def internet_provider_member_detail(request, public_code):
    partner = request.internet_partner_association.partner
    member = get_object_or_404(
        Member.objects.filter(internet_entitlements__partner=partner).distinct(),
        public_code=public_code,
    )
    entitlements = _provider_entitlements(partner).filter(member=member).select_related(
        'package', 'subscription', 'revenue_share',
    ).prefetch_related('devices').order_by('-created_at')
    sessions = _provider_sessions(partner).filter(member=member).select_related(
        'package', 'entitlement',
    ).order_by('-start_time')[:50]
    subscriptions = MembershipSubscription.objects.filter(
        member=member, internet_entitlements__partner=partner,
    ).select_related('plan').distinct().order_by('-starts_at')
    return render(request, 'provider/member_detail.html', _base_context(
        request, member=member, entitlements=entitlements,
        sessions=sessions, subscriptions=subscriptions,
    ))


@provider_capability_required('internet_view_subscriptions')
def internet_provider_subscriptions(request):
    partner = request.internet_partner_association.partner
    status = request.GET.get('status', '').strip()
    query = request.GET.get('q', '').strip()
    entitlements = _provider_entitlements(partner).select_related(
        'member', 'package', 'subscription', 'revenue_share',
    ).prefetch_related('devices').order_by('-created_at')
    if status in InternetEntitlement.Status.values:
        entitlements = entitlements.filter(status=status)
    if query:
        search = (
            Q(access_code__icontains=query) | Q(member__name_ar__icontains=query)
            | Q(guest_name__icontains=query) | Q(package__name_ar__icontains=query)
        )
        if request.internet_partner_association.can_view_customer_phone:
            search |= Q(member__phone__icontains=query) | Q(guest_phone__icontains=query)
        entitlements = entitlements.filter(search)
    return render(request, 'provider/subscriptions.html', _base_context(
        request,
        entitlements=entitlements,
        status_choices=InternetEntitlement.Status.choices,
        filters={'status': status, 'q': query},
    ))


@provider_capability_required('internet_view_subscriptions')
def internet_provider_subscription_detail(request, public_code):
    partner = request.internet_partner_association.partner
    entitlement = get_object_or_404(
        _provider_entitlements(partner).select_related(
            'member', 'package', 'subscription', 'revenue_share',
        ).prefetch_related('devices', 'network_operations'),
        public_code=public_code,
    )
    sessions = entitlement.sessions.select_related('member', 'package').order_by('-start_time')
    return render(request, 'provider/subscription_detail.html', _base_context(
        request,
        entitlement=entitlement,
        sessions=sessions,
        used_today=daily_minutes_used(entitlement),
        remaining_today=daily_minutes_remaining(entitlement),
    ))


@require_POST
@provider_capability_required('internet_manage_subscriptions')
def internet_provider_subscription_action(request, public_code):
    partner = request.internet_partner_association.partner
    entitlement = get_object_or_404(_provider_entitlements(partner), public_code=public_code)
    action = request.POST.get('action', '').strip()
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'سبب الإجراء مطلوب لتسجيله في سجل التدقيق.')
        return redirect('internet_provider_subscription_detail', public_code=public_code)
    try:
        if action == 'suspend':
            suspend_internet_entitlement(entitlement, actor=request.user, reason=reason)
            messages.success(request, 'تم تعليق اشتراك الإنترنت وإنهاء جلساته الفعالة.')
        elif action == 'resume':
            resume_internet_entitlement(entitlement, actor=request.user, reason=reason)
            messages.success(request, 'تم استئناف اشتراك الإنترنت وإرسال طلب تحديث للشبكة.')
        else:
            messages.error(request, 'إجراء غير معروف.')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('internet_provider_subscription_detail', public_code=public_code)


@provider_capability_required('internet_view_sessions')
def internet_provider_sessions(request):
    partner = request.internet_partner_association.partner
    status = request.GET.get('status', '').strip()
    query = request.GET.get('q', '').strip()
    sessions = _provider_sessions(partner).select_related(
        'member', 'package', 'entitlement',
    ).order_by('-start_time')
    if status in InternetSession.Status.values:
        sessions = sessions.filter(status=status)
    if query:
        search = (
            Q(member__name_ar__icontains=query) | Q(guest_name__icontains=query)
            | Q(access_code__icontains=query) | Q(package__name_ar__icontains=query)
        )
        if request.internet_partner_association.can_view_customer_phone:
            search |= Q(member__phone__icontains=query) | Q(guest_phone__icontains=query)
        sessions = sessions.filter(search)
    return render(request, 'provider/sessions.html', _base_context(
        request,
        sessions=sessions,
        status_choices=InternetSession.Status.choices,
        filters={'status': status, 'q': query},
    ))


@provider_capability_required('internet_view_sessions')
def internet_provider_session_detail(request, public_code):
    partner = request.internet_partner_association.partner
    session = get_object_or_404(
        _provider_sessions(partner).select_related('member', 'package', 'entitlement'),
        public_code=public_code,
    )
    return render(request, 'provider/session_detail.html', _base_context(request, session=session))


@require_POST
@provider_capability_required('internet_manage_sessions')
def internet_provider_session_end(request, public_code):
    partner = request.internet_partner_association.partner
    session = get_object_or_404(_provider_sessions(partner), public_code=public_code)
    if session.status == InternetSession.Status.ACTIVE:
        try:
            session = end_usage_session(session, actor=request.user)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            ActivityLog.objects.create(
                actor=request.user,
                action='internet.provider_session_ended',
                details={'session_public_code': str(session.public_code)},
            )
            messages.success(request, 'تم إنهاء جلسة الإنترنت وتسجيل الاستهلاك.')
    return redirect('internet_provider_session_detail', public_code=public_code)


@provider_capability_required('internet_view_packages')
def internet_provider_packages(request):
    partner = request.internet_partner_association.partner
    packages = _provider_packages(partner).select_related('bandwidth_profile').order_by('sort_order', 'name_ar')
    return render(request, 'provider/packages.html', _base_context(request, packages=packages))


@provider_capability_required('internet_manage_packages')
def internet_provider_package_edit(request, public_code=None):
    partner = request.internet_partner_association.partner
    package = (
        get_object_or_404(_provider_packages(partner), public_code=public_code)
        if public_code else InternetPackage(partner=partner)
    )
    before = {
        field: getattr(package, field, None)
        for field in ProviderPackageForm.Meta.fields
    }
    form = ProviderPackageForm(request.POST or None, instance=package)
    if request.method == 'POST' and form.is_valid():
        package = form.save(commit=False)
        if package.pk is None:
            package.partner = partner
        package.save()
        changed = [
            field for field in form.changed_data
            if before.get(field) != getattr(package, field, None)
        ]
        ActivityLog.objects.create(
            actor=request.user,
            action='internet.provider_package_changed',
            details={'package_public_code': str(package.public_code), 'fields_changed': changed},
        )
        messages.success(request, 'تم حفظ باقة الإنترنت. التغييرات تطبق على الاشتراكات الجديدة فقط.')
        return redirect('internet_provider_packages')
    return render(request, 'provider/package_form.html', _base_context(
        request, form=form, package=package if package.pk else None,
    ))


@provider_capability_required('internet_view_network')
def internet_provider_network(request):
    partner = request.internet_partner_association.partner
    packages = _provider_packages(partner).select_related('bandwidth_profile')
    profile_ids = packages.exclude(bandwidth_profile__isnull=True).values_list('bandwidth_profile_id', flat=True)
    networks = WifiNetwork.objects.filter(is_active=True).select_related('bandwidth_profile').order_by('name_ar')
    entitlement_operations = InternetNetworkOperation.objects.filter(
        entitlement__partner=partner,
    ).select_related('entitlement', 'entitlement__member', 'entitlement__package').order_by('-updated_at')[:30]
    state = InternetOperationsState.objects.filter(key='default').first()
    return render(request, 'provider/network.html', _base_context(
        request,
        networks=networks,
        profiles=InternetBandwidthProfile.objects.filter(pk__in=profile_ids).order_by('name'),
        operations=entitlement_operations,
        operations_state=state,
        worker_fresh=worker_is_fresh(state),
        readiness=internet_readiness_report(),
        preflight=mikrotik_enablement_preflight(),
        guest_wifi_policy=GuestWifiPolicy.objects.filter(key='default').first(),
    ))


@require_POST
@provider_capability_required('internet_manage_network')
def internet_provider_network_healthcheck(request):
    ok, message = run_readonly_mikrotik_healthcheck(actor=request.user)
    (messages.success if ok else messages.error)(request, message)
    return redirect('internet_provider_network')


@provider_capability_required('internet_view_reports')
def internet_provider_reports(request):
    form = _date_range_form(request, default_days=29)
    if not form.is_valid():
        return render(request, 'provider/reports.html', _base_context(
            request, date_range_form=form,
        ), status=400)
    start, end = form.cleaned_data['start'], form.cleaned_data['end']
    shares, totals = _revenue_report(request.internet_partner_association.partner, start, end)
    return render(request, 'provider/reports.html', _base_context(
        request,
        date_range_form=form,
        start=start,
        end=end,
        shares=shares,
        totals=totals,
    ))


@provider_capability_required('internet_view_reports')
def internet_provider_reports_csv(request):
    form = _date_range_form(request, default_days=29)
    if not form.is_valid():
        return HttpResponseBadRequest('نطاق التقرير غير صالح.')
    start, end = form.cleaned_data['start'], form.cleaned_data['end']
    shares, _ = _revenue_report(request.internet_partner_association.partner, start, end)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="internet-provider-{start}-{end}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([
        'التاريخ', 'العميل', 'الهاتف', 'الباقة', 'القسيمة',
        'الإجمالي', 'حصة المزوّد', 'حصة هَبّ',
    ])
    show_phone = request.internet_partner_association.can_view_customer_phone
    for share in shares:
        entitlement = share.entitlement
        customer = entitlement.member.name_ar if entitlement.member_id else (entitlement.guest_name or 'زائر')
        phone = (
            entitlement.member.phone if entitlement.member_id else entitlement.guest_phone
        ) if show_phone else ''
        writer.writerow([
            share.business_date,
            customer,
            phone,
            share.package.name_ar if share.package_id else 'ميزة عضوية',
            entitlement.access_code,
            share.gross_amount_syp,
            share.partner_amount_syp,
            share.hub_amount_syp,
        ])
    return response


@provider_capability_required('provider_dashboard')
def internet_provider_settings(request):
    capabilities = get_staff_capabilities(request.user)
    rows = [
        {'code': code, 'label': CAPABILITY_LABELS[code], 'allowed': capabilities.get(code, False)}
        for code in PROVIDER_CAPABILITIES
    ]
    return render(request, 'provider/settings.html', _base_context(
        request, capability_rows=rows,
    ))


@provider_capability_required('internet_view_reports')
def legacy_internet_partner_dashboard(request):
    """Preserve the former one-page report while /provider/ becomes canonical."""
    form = _date_range_form(request)
    association = request.internet_partner_association
    raw_start = request.GET.get('start', '')
    raw_end = request.GET.get('end', '')
    if not form.is_valid():
        return render(request, 'partner/internet_dashboard.html', {
            'association': association,
            'date_range_form': form,
            'start': raw_start,
            'end': raw_end,
        }, status=400)
    start, end = form.cleaned_data['start'], form.cleaned_data['end']
    entitlements = _provider_entitlements(association.partner).select_related(
        'member', 'member__default_plan', 'package', 'payment', 'revenue_share',
    ).prefetch_related('devices', 'sessions').order_by('-created_at')
    shares, totals = _revenue_report(association.partner, start, end)
    return render(request, 'partner/internet_dashboard.html', {
        'association': association,
        'entitlements': entitlements,
        'shares': shares,
        'totals': totals,
        'start': start,
        'end': end,
        'date_range_form': form,
    })
