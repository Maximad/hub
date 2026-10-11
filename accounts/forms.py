import uuid

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db.models import Q

from accounts.models import AccessProfile, UserCapabilityOverride
from accounts.permissions import CAPABILITY_LABELS, get_capability_overrides, is_owner_or_admin

User = get_user_model()


class StaffUserBaseForm(forms.ModelForm):
    allow_django_admin_access = forms.BooleanField(label='السماح بدخول Django admin (/admin/)', required=False)
    make_superuser = forms.BooleanField(label='جعله Superuser', required=False)
    can_view_customer_phone = forms.BooleanField(
        label='السماح بعرض أرقام هواتف عملاء الإنترنت', required=False,
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'job_title', 'access_profile', 'is_active']
        labels = {
            'username': 'اسم المستخدم',
            'first_name': 'الاسم الأول',
            'last_name': 'الاسم الأخير',
            'email': 'البريد الإلكتروني',
            'phone': 'الهاتف',
            'job_title': 'المسمى الوظيفي',
            'access_profile': 'نمط الوصول',
            'is_active': 'نشط',
        }

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'hub-input')
        self.fields['is_active'].initial = True
        self.fields['phone'].required = False
        self.fields['access_profile'].required = True
        profile_qs = AccessProfile.objects.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.access_profile_id:
            profile_qs = AccessProfile.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.access_profile_id)
            )
        self.fields['access_profile'].queryset = profile_qs.order_by('name_ar', 'code')
        self.fields['access_profile'].help_text = (
            'يحدد الصلاحيات الأساسية. المسمى الوظيفي مستقل عنه ويمكن أن يكون مصوراً أو مدير فعاليات أو غير ذلك.'
        )
        if self.instance and self.instance.pk:
            self.fields['allow_django_admin_access'].initial = self.instance.is_staff
            self.fields['make_superuser'].initial = self.instance.is_superuser
        from core.models import InternetPartner, InternetPartnerUser
        self.fields['internet_partner'] = forms.ModelChoiceField(
            label='مزوّد الإنترنت المرتبط',
            queryset=InternetPartner.objects.filter(active=True).order_by('name'),
            required=False,
            help_text='مطلوب فقط لدور مزوّد الإنترنت ويحدد البيانات التي يمكن للحساب رؤيتها.',
            widget=forms.Select(attrs={'class': 'hub-input'}),
        )
        if self.instance and self.instance.pk:
            association = InternetPartnerUser.objects.filter(user=self.instance).select_related('partner').first()
            if association:
                self.fields['internet_partner'].initial = association.partner
                self.fields['can_view_customer_phone'].initial = association.can_view_customer_phone
        if not (actor and actor.is_superuser):
            self.fields.pop('make_superuser', None)
        if not is_owner_or_admin(actor):
            self.fields.pop('allow_django_admin_access', None)

    def clean_make_superuser(self):
        return bool(self.cleaned_data.get('make_superuser')) if self.actor and self.actor.is_superuser else False

    def clean_allow_django_admin_access(self):
        return bool(self.cleaned_data.get('allow_django_admin_access')) if is_owner_or_admin(self.actor) else False

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone:
            return phone
        return f'no-phone-{uuid.uuid4().hex[:12]}'

    def clean(self):
        cleaned = super().clean()
        profile = cleaned.get('access_profile')
        if (
            profile
            and profile.base_role == AccessProfile.BaseRole.INTERNET_PROVIDER
            and not cleaned.get('internet_partner')
        ):
            self.add_error('internet_partner', 'اختر مزوّد الإنترنت المرتبط بهذا الحساب.')
        return cleaned

    def _sync_provider_association(self, user):
        from core.models import InternetPartnerUser
        partner = self.cleaned_data.get('internet_partner')
        if user.role != User.Role.INTERNET_PROVIDER or partner is None:
            InternetPartnerUser.objects.filter(user=user).delete()
            return
        InternetPartnerUser.objects.filter(user=user).exclude(partner=partner).delete()
        InternetPartnerUser.objects.update_or_create(
            user=user,
            partner=partner,
            defaults={'can_view_customer_phone': bool(self.cleaned_data.get('can_view_customer_phone'))},
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        profile = self.cleaned_data.get('access_profile')
        if profile is not None:
            user.access_profile = profile
            user.role = profile.base_role
        user.is_staff = bool(self.cleaned_data.get('allow_django_admin_access', False))
        user.is_superuser = bool(self.cleaned_data.get('make_superuser', False)) if self.actor and self.actor.is_superuser else bool(getattr(user, 'is_superuser', False))
        if user.is_superuser:
            user.is_staff = True
            user.role = User.Role.ADMIN
        elif user.role == User.Role.INTERNET_PROVIDER:
            # Provider authentication has its own portal and must never imply
            # access to Django admin.
            user.is_staff = False
            user.is_superuser = False
        if commit:
            user.save()
            self.save_m2m()
            self._sync_provider_association(user)
        return user


class StaffUserCreateForm(StaffUserBaseForm):
    password = forms.CharField(label='كلمة المرور', widget=forms.PasswordInput(attrs={'class': 'hub-input'}))
    confirm_password = forms.CharField(label='تأكيد كلمة المرور', widget=forms.PasswordInput(attrs={'class': 'hub-input'}))

    class Meta(StaffUserBaseForm.Meta):
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'job_title', 'access_profile', 'is_active']

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        confirm = cleaned.get('confirm_password')
        if password and confirm and password != confirm:
            self.add_error('confirm_password', 'كلمتا المرور غير متطابقتين.')
        if password:
            password_validation.validate_password(password, self.instance)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if user.role in {
            User.Role.CASHIER, User.Role.WAITER, User.Role.KITCHEN,
            User.Role.BARTENDER, User.Role.INTERNET_PROVIDER,
        }:
            user.is_staff = False
            user.is_superuser = False
        if commit:
            user.save()
            self.save_m2m()
            self._sync_provider_association(user)
        return user


class StaffUserEditForm(StaffUserBaseForm):
    capability_allow = forms.MultipleChoiceField(
        label='صلاحيات إضافية لهذا المستخدم',
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='تتجاوز نمط الوصول وتمنح الصلاحيات المحددة لهذا المستخدم فقط.',
    )
    capability_deny = forms.MultipleChoiceField(
        label='صلاحيات ممنوعة لهذا المستخدم',
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='تتجاوز نمط الوصول وتمنع الصلاحيات المحددة لهذا المستخدم فقط.',
    )

    class Meta(StaffUserBaseForm.Meta):
        fields = ['first_name', 'last_name', 'email', 'phone', 'job_title', 'access_profile', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = list(CAPABILITY_LABELS.items())
        self.fields['capability_allow'].choices = choices
        self.fields['capability_deny'].choices = choices
        if self.instance and self.instance.pk:
            overrides = get_capability_overrides(self.instance)
            self.fields['capability_allow'].initial = [name for name, allowed in overrides.items() if allowed]
            self.fields['capability_deny'].initial = [name for name, allowed in overrides.items() if not allowed]

    def clean_is_active(self):
        is_active = self.cleaned_data['is_active']
        if self.instance.pk and self.actor and self.instance.pk == self.actor.pk and not is_active:
            raise ValidationError('لا يمكنك تعطيل حسابك الحالي.')
        return is_active

    def clean(self):
        cleaned = super().clean()
        allowed = set(cleaned.get('capability_allow') or [])
        denied = set(cleaned.get('capability_deny') or [])
        overlap = allowed & denied
        if overlap:
            labels = [CAPABILITY_LABELS.get(name, name) for name in sorted(overlap)]
            raise ValidationError('لا يمكن منح ومنع الصلاحية نفسها: ' + '، '.join(labels))
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            allowed = set(self.cleaned_data.get('capability_allow') or [])
            denied = set(self.cleaned_data.get('capability_deny') or [])
            UserCapabilityOverride.objects.filter(user=user).delete()
            UserCapabilityOverride.objects.bulk_create([
                UserCapabilityOverride(user=user, capability=name, allowed=True)
                for name in sorted(allowed)
            ] + [
                UserCapabilityOverride(user=user, capability=name, allowed=False)
                for name in sorted(denied)
            ])
        return user


class StaffUserPasswordForm(forms.Form):
    new_password = forms.CharField(label='كلمة المرور الجديدة', widget=forms.PasswordInput(attrs={'class': 'hub-input'}))
    confirm_password = forms.CharField(label='تأكيد كلمة المرور', widget=forms.PasswordInput(attrs={'class': 'hub-input'}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('new_password')
        confirm = cleaned.get('confirm_password')
        if password and confirm and password != confirm:
            self.add_error('confirm_password', 'كلمتا المرور غير متطابقتين.')
        if password:
            password_validation.validate_password(password, self.user)
        return cleaned


class CustomUserCreationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['phone'].required = False
        self.fields['access_profile'].required = True
        self.fields['access_profile'].queryset = AccessProfile.objects.filter(
            is_active=True
        ).order_by('name_ar', 'code')

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        return phone or f'no-phone-{uuid.uuid4().hex[:12]}'

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            'username', 'email', 'phone', 'job_title', 'access_profile',
            'is_staff', 'is_superuser', 'is_active',
        )
        labels = {
            'phone': 'الهاتف',
            'job_title': 'المسمى الوظيفي',
            'access_profile': 'نمط الوصول',
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.access_profile_id:
            user.role = user.access_profile.base_role
        if commit:
            user.save()
            self.save_m2m()
        return user


class CustomUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'access_profile' in self.fields:
            self.fields['access_profile'].required = True
            qs = AccessProfile.objects.filter(is_active=True)
            if self.instance and self.instance.pk and self.instance.access_profile_id:
                qs = AccessProfile.objects.filter(
                    Q(is_active=True) | Q(pk=self.instance.access_profile_id)
                )
            self.fields['access_profile'].queryset = qs.order_by('name_ar', 'code')

    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'
        help_texts = {
            'is_staff': 'is_staff = صلاحية دخول لوحة Django admin التقنية.',
            'access_profile': 'نمط الوصول يحدد الصلاحيات الأساسية داخل Hub.',
            'job_title': 'المسمى الوظيفي وصفي فقط ولا يمنح صلاحيات.',
            'role': 'حقل توافق داخلي يُزامن تلقائياً من نمط الوصول.',
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.access_profile_id:
            user.role = user.access_profile.base_role
        if commit:
            user.save()
            self.save_m2m()
        return user
