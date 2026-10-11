from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html

from accounts.forms import CustomUserChangeForm, CustomUserCreationForm
from accounts.models import AccessProfile, User
from accounts.permissions import CAPABILITY_LABELS


class AccessProfileAdminForm(forms.ModelForm):
    capability_choices = forms.MultipleChoiceField(
        label='الصلاحيات الأساسية',
        required=False,
        choices=list(CAPABILITY_LABELS.items()),
        widget=forms.CheckboxSelectMultiple,
        help_text='هذه هي الصلاحيات التي يحصل عليها كل مستخدم يختار هذا النمط، قبل الاستثناءات الفردية.',
    )

    class Meta:
        model = AccessProfile
        fields = (
            'name_ar', 'code', 'description', 'base_role',
            'capability_choices', 'is_active', 'is_system',
        )
        labels = {
            'name_ar': 'اسم نمط الوصول',
            'code': 'الرمز التقني',
            'description': 'الوصف',
            'base_role': 'المسار التشغيلي الداخلي',
            'is_active': 'متاح للاختيار',
            'is_system': 'نمط نظامي',
        }
        help_texts = {
            'base_role': (
                'للتوافق مع الإشعارات والمسارات القديمة فقط. لمعظم المسميات الجديدة استخدم «فريق عام».'
            ),
            'is_system': 'تمييز فقط للنماذج الأساسية التي أنشأها النظام، ويمكن تعديل صلاحياتها.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['capability_choices'].initial = list(
            getattr(self.instance, 'capabilities', None) or []
        )

    def clean_capability_choices(self):
        allowed = set(CAPABILITY_LABELS)
        return [value for value in self.cleaned_data.get('capability_choices', []) if value in allowed]

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.capabilities = sorted(set(self.cleaned_data.get('capability_choices') or []))
        if commit:
            profile.save()
        return profile


@admin.register(AccessProfile)
class AccessProfileAdmin(admin.ModelAdmin):
    form = AccessProfileAdminForm
    list_display = ('name_ar', 'code', 'base_role', 'capability_count', 'is_active', 'is_system', 'updated_at')
    list_filter = ('is_active', 'is_system', 'base_role')
    search_fields = ('name_ar', 'code', 'description')
    ordering = ('name_ar',)

    @admin.display(description='عدد الصلاحيات')
    def capability_count(self, obj):
        return len(obj.capabilities or [])


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    list_display = (
        'username', 'email', 'phone', 'job_title', 'access_profile',
        'is_active', 'is_staff', 'is_superuser', 'last_login', 'view_as_link',
    )
    list_filter = ('access_profile', 'is_staff', 'is_superuser', 'is_active')
    search_fields = (
        'username', 'email', 'phone', 'first_name', 'last_name',
        'job_title', 'access_profile__name_ar',
    )
    ordering = ('username',)
    readonly_fields = ('role',)
    fieldsets = UserAdmin.fieldsets + (
        ('Hub Sweida — الهوية والوصول', {
            'fields': ('phone', 'job_title', 'access_profile', 'preferred_language', 'role'),
            'description': (
                'المسمى الوظيفي وصفي فقط. نمط الوصول يحدد الصلاحيات. '
                'الحقل role مسار توافق داخلي ويُزامن تلقائياً.'
            ),
        }),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username', 'email', 'phone', 'job_title', 'access_profile',
                'password1', 'password2', 'is_active', 'is_staff', 'is_superuser',
            ),
            'description': (
                'اختر نمط الوصول بحسب ما يحتاجه الحساب داخل النظام. '
                'يمكن إدارة أنماط الوصول من Accounts → Access profiles.'
            ),
        }),
    )

    @admin.display(description='معاينة')
    def view_as_link(self, obj):
        if not obj.pk or not obj.is_active or obj.is_superuser:
            return '—'
        return format_html(
            '<a class="button" href="{}">View As</a>',
            reverse('staff_user_view_as', kwargs={'user_id': obj.pk}),
        )
