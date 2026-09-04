from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.forms import CustomUserChangeForm, CustomUserCreationForm
from .models import StaffCapabilityOverride, User


class StaffCapabilityOverrideInline(admin.TabularInline):
    model = StaffCapabilityOverride
    extra = 0
    fields = ('capability', 'allowed', 'updated_at')
    readonly_fields = ('updated_at',)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    inlines = (StaffCapabilityOverrideInline,)
    list_display = ('username', 'email', 'phone', 'role', 'is_active', 'is_staff', 'is_superuser', 'last_login')
    list_filter = ('role', 'is_staff', 'is_superuser', 'is_active')
    search_fields = ('username', 'email', 'phone', 'first_name', 'last_name')
    ordering = ('username',)
    fieldsets = UserAdmin.fieldsets + (
        ('صلاحيات Hub/Masharib', {
            'fields': ('phone', 'role', 'preferred_language'),
            'description': 'role = الدور الافتراضي داخل Hub/Masharib. الاستثناءات الفردية تظهر أسفل الصفحة. is_staff = صلاحية دخول لوحة Django admin التقنية.',
        }),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'phone', 'role', 'password1', 'password2', 'is_active', 'is_staff', 'is_superuser'),
            'description': 'is_staff = دخول /admin/ فقط. role = الصلاحيات الافتراضية داخل صفحات /staff/.',
        }),
    )


@admin.register(StaffCapabilityOverride)
class StaffCapabilityOverrideAdmin(admin.ModelAdmin):
    list_display = ('user', 'capability', 'allowed', 'updated_at')
    list_filter = ('allowed', 'capability')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'capability')
    autocomplete_fields = ('user',)
