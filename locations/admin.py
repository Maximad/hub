from django import forms
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from core.models import TableArea
from .models import TableAreaSettings, normalize_table_entry_code


class TableAreaAdminForm(forms.ModelForm):
    customer_entry_code = forms.CharField(
        label='رقم الطاولة للزبون',
        required=False,
        help_text='الرقم الذي يكتبه الزبون عند الدخول من لابتوب أو بدون QR.',
    )
    staff_description = forms.CharField(
        label='وصف داخلي للموظفين',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='مثال: الطاولة المدورة بجانب البار. لا يظهر هذا النص للزبون.',
    )

    class Meta:
        model = TableArea
        fields = ('room', 'name_ar', 'name_en')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        settings_obj = None
        if self.instance and self.instance.pk:
            settings_obj = TableAreaSettings.objects.filter(table=self.instance).first()
        if settings_obj:
            self.fields['customer_entry_code'].initial = settings_obj.customer_entry_code or ''
            self.fields['staff_description'].initial = settings_obj.staff_description

    def clean_customer_entry_code(self):
        raw = self.cleaned_data.get('customer_entry_code')
        if not raw:
            return ''
        code = normalize_table_entry_code(raw)
        duplicate = TableAreaSettings.objects.filter(customer_entry_code=code)
        if self.instance and self.instance.pk:
            duplicate = duplicate.exclude(table=self.instance)
        if duplicate.exists():
            raise ValidationError('رقم الطاولة مستخدم لطاولة أخرى.')
        return code


try:
    admin.site.unregister(TableArea)
except NotRegistered:
    pass


@admin.register(TableArea)
class TableAreaAdmin(admin.ModelAdmin):
    form = TableAreaAdminForm
    list_display = (
        'customer_entry_code_display',
        'name_ar',
        'room',
        'staff_description_display',
        'qr_menu_link',
    )
    list_filter = ('room',)
    search_fields = (
        'name_ar',
        'name_en',
        'room__name_ar',
        'access_settings__customer_entry_code',
        'access_settings__staff_description',
    )
    readonly_fields = ('qr_token', 'qr_menu_link')
    autocomplete_fields = ('room',)
    fields = (
        'room',
        'customer_entry_code',
        'name_ar',
        'name_en',
        'staff_description',
        'qr_token',
        'qr_menu_link',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('room', 'access_settings')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        TableAreaSettings.objects.update_or_create(
            table=obj,
            defaults={
                'customer_entry_code': form.cleaned_data.get('customer_entry_code') or None,
                'staff_description': form.cleaned_data.get('staff_description') or '',
            },
        )

    @admin.display(description='رقم الطاولة', ordering='access_settings__customer_entry_code')
    def customer_entry_code_display(self, obj):
        try:
            return obj.access_settings.customer_entry_code or '—'
        except TableAreaSettings.DoesNotExist:
            return '—'

    @admin.display(description='وصف الموظفين')
    def staff_description_display(self, obj):
        try:
            description = obj.access_settings.staff_description.strip()
        except TableAreaSettings.DoesNotExist:
            description = ''
        if not description:
            return '—'
        return description if len(description) <= 60 else f'{description[:57]}…'

    @admin.display(description='رابط منيو QR')
    def qr_menu_link(self, obj):
        if not obj or not obj.qr_token:
            return '—'
        return format_html(
            '<a href="/menu/table/{}/" target="_blank">/menu/table/{}/</a>',
            obj.qr_token,
            obj.qr_token,
        )
