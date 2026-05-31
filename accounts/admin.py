from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.forms import CustomUserChangeForm, CustomUserCreationForm
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    list_display = ('username', 'email', 'phone', 'role', 'is_active', 'is_staff', 'is_superuser', 'last_login')
    list_filter = ('role', 'is_staff', 'is_superuser', 'is_active')
    search_fields = ('username', 'email', 'phone', 'first_name', 'last_name')
    ordering = ('username',)
    fieldsets = UserAdmin.fieldsets + (
        ('صلاحيات Hub/Masharib', {
            'fields': ('phone', 'role', 'preferred_language'),
            'description': 'role = دور وصلاحيات المستخدم داخل Hub/Masharib. is_staff = صلاحية دخول لوحة Django admin التقنية.',
        }),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'phone', 'role', 'password1', 'password2', 'is_active', 'is_staff', 'is_superuser'),
            'description': 'is_staff = دخول /admin/ فقط. role = صلاحيات Hub/Masharib داخل صفحات /staff/.',
        }),
    )
