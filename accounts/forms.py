import uuid

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError

from accounts.models import StaffCapabilityOverride
from accounts.permissions import (
    CAPABILITY_LABELS,
    clear_staff_capability_cache,
    is_owner_or_admin,
    role_default_has_capability,
)

User = get_user_model()


def _capability_field_name(capability):
    return f"capability_{capability.replace('/', '__')}"


class StaffUserBaseForm(forms.ModelForm):
    allow_django_admin_access = forms.BooleanField(label='السماح بدخول Django admin (/admin/)', required=False)
    make_superuser = forms.BooleanField(label='جعله Superuser', required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active']
        labels = {
            'username': 'اسم المستخدم',
            'first_name': 'الاسم الأول',
            'last_name': 'اسم العائلة',
            'email': 'البريد الإلكتروني',
            'phone': 'الهاتف',
            'role': 'الدور داخل Hub/Masharib',
            'is_active': 'نشط',
        }

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'hub-input')
        self.fields['is_active'].initial = True
        self.fields['phone'].required = False
        if self.instance and self.instance.pk:
            self.fields['allow_django_admin_access'].initial = self.instance.is_staff
            self.fields['make_superuser'].initial = self.instance.is_superuser
        if not (actor and actor.is_superuser):
            self.fields.pop('make_superuser', None)
        if not is_owner_or_admin(actor):
            self.fields.pop('allow_django_admin_access', None)

        # Keep the ordinary account fields and the capability matrix visually
        # separate in the staff template.
        self.basic_field_names = list(self.fields.keys())
        self.capability_field_names = []
        self._capability_field_map = {}

        override_map = {}
        if self.instance and self.instance.pk:
            override_map = {
                row.capability: row.allowed
                for row in self.instance.staff_capability_overrides.all()
            }

        selected_role = (
            self.data.get('role') if self.is_bound else getattr(self.instance, 'role', User.Role.WAITER)
        ) or User.Role.WAITER
        fixed_admin = bool(
            self.instance
            and self.instance.pk
            and (self.instance.is_superuser or self.instance.role == User.Role.ADMIN)
        )

        for capability, label in CAPABILITY_LABELS.items():
            field_name = _capability_field_name(capability)
            default_allowed = role_default_has_capability(selected_role, capability)
            field = forms.ChoiceField(
                label=label,
                required=False,
                choices=(
                    ('inherit', 'حسب الدور'),
                    ('allow', 'سماح لهذا المستخدم'),
                    ('deny', 'منع لهذا المستخدم'),
                ),
                initial=(
                    'allow' if override_map.get(capability) is True
                    else 'deny' if override_map.get(capability) is False
                    else 'inherit'
                ),
                help_text=(
                    'المدير يحصل على جميع صلاحيات Hub تلقائياً.'
                    if fixed_admin
                    else f"افتراضي الدور الحالي: {'سماح' if default_allowed else 'منع'}."
                ),
                widget=forms.Select(attrs={'class': 'hub-input'}),
                disabled=fixed_admin,
            )
            self.fields[field_name] = field
            self.capability_field_names.append(field_name)
            self._capability_field_map[field_name] = capability

    def clean_make_superuser(self):
        return bool(self.cleaned_data.get('make_superuser')) if self.actor and self.actor.is_superuser else False

    def clean_allow_django_admin_access(self):
        return bool(self.cleaned_data.get('allow_django_admin_access')) if is_owner_or_admin(self.actor) else False

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone:
            return phone
        return f'no-phone-{uuid.uuid4().hex[:12]}'

    def _sync_capability_overrides(self, user):
        # Admin is a deliberately full-access role. Clear stale overrides when a
        # user is promoted so the matrix cannot create surprising admin denies.
        if user.is_superuser or user.role == User.Role.ADMIN:
            user.staff_capability_overrides.all().delete()
            clear_staff_capability_cache(user)
            return

        for field_name, capability in self._capability_field_map.items():
            value = self.cleaned_data.get(field_name, 'inherit') or 'inherit'
            if value == 'inherit':
                StaffCapabilityOverride.objects.filter(
                    user=user,
                    capability=capability,
                ).delete()
                continue
            StaffCapabilityOverride.objects.update_or_create(
                user=user,
                capability=capability,
                defaults={'allowed': value == 'allow'},
            )
        clear_staff_capability_cache(user)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = bool(self.cleaned_data.get('allow_django_admin_access', False))
        user.is_superuser = bool(self.cleaned_data.get('make_superuser', False)) if self.actor and self.actor.is_superuser else bool(getattr(user, 'is_superuser', False))
        if user.is_superuser:
            user.is_staff = True
            user.role = User.Role.ADMIN
        if commit:
            user.save()
            self.save_m2m()
            self._sync_capability_overrides(user)
        return user


class StaffUserCreateForm(StaffUserBaseForm):
    password = forms.CharField(label='كلمة المرور', widget=forms.PasswordInput(attrs={'class': 'hub-input'}))
    confirm_password = forms.CharField(label='تأكيد كلمة المرور', widget=forms.PasswordInput(attrs={'class': 'hub-input'}))

    class Meta(StaffUserBaseForm.Meta):
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active']

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
        if user.role in {User.Role.CASHIER, User.Role.WAITER, User.Role.KITCHEN}:
            user.is_staff = False
            user.is_superuser = False
        if commit:
            user.save()
            self.save_m2m()
            self._sync_capability_overrides(user)
        return user


class StaffUserEditForm(StaffUserBaseForm):
    class Meta(StaffUserBaseForm.Meta):
        fields = ['first_name', 'last_name', 'email', 'phone', 'role', 'is_active']

    def clean_is_active(self):
        is_active = self.cleaned_data['is_active']
        if self.instance.pk and self.actor and self.instance.pk == self.actor.pk and not is_active:
            raise ValidationError('لا يمكنك تعطيل حسابك الحالي.')
        return is_active


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

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        return phone or f'no-phone-{uuid.uuid4().hex[:12]}'

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'phone', 'role', 'is_staff', 'is_superuser', 'is_active')
        labels = {'phone': 'الهاتف', 'role': 'دور Hub/Masharib'}


class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'
        help_texts = {
            'is_staff': 'is_staff = صلاحية دخول لوحة Django admin التقنية.',
            'role': 'role = دور وصلاحيات المستخدم داخل Hub/Masharib.',
        }
