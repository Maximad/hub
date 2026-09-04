import uuid

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError

from accounts.models import UserCapabilityOverride
from accounts.permissions import CAPABILITY_LABELS, get_capability_overrides, is_owner_or_admin

User = get_user_model()


class StaffUserBaseForm(forms.ModelForm):
    allow_django_admin_access = forms.BooleanField(label='السماح بدخول Django admin (/admin/)', required=False)
    make_superuser = forms.BooleanField(label='جعله Superuser', required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active']
        labels = {
            'username': 'اسم المستخدم',
            'first_name': 'الاسم الأول',
            'last_name': 'الاسم الأخير',
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

    def clean_make_superuser(self):
        return bool(self.cleaned_data.get('make_superuser')) if self.actor and self.actor.is_superuser else False

    def clean_allow_django_admin_access(self):
        return bool(self.cleaned_data.get('allow_django_admin_access')) if is_owner_or_admin(self.actor) else False

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone:
            return phone
        return f'no-phone-{uuid.uuid4().hex[:12]}'

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
        return user


class StaffUserEditForm(StaffUserBaseForm):
    capability_allow = forms.MultipleChoiceField(
        label='صلاحيات إضافية لهذا المستخدم',
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='تتجاوز افتراضات الدور وتمنح الصلاحيات المحددة لهذا المستخدم فقط.',
    )
    capability_deny = forms.MultipleChoiceField(
        label='صلاحيات ممنوعة لهذا المستخدم',
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='تتجاوز افتراضات الدور وتمنع الصلاحيات المحددة لهذا المستخدم فقط.',
    )

    class Meta(StaffUserBaseForm.Meta):
        fields = ['first_name', 'last_name', 'email', 'phone', 'role', 'is_active']

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
