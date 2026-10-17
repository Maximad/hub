"""Django admin is reserved for active superusers.

Staff permissions and Hub operational dashboards remain independent of
Django's `is_staff` flag. Both login and admin endpoints enforce this policy.
"""
from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.admin.sites import AdminSite


class SuperuserAdminAuthenticationForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise forms.ValidationError(
                self.error_messages['invalid_login'],
                code='invalid_login',
                params={'username': self.username_field.verbose_name},
            )


class SuperuserAdminSite(AdminSite):
    login_form = SuperuserAdminAuthenticationForm

    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_superuser
