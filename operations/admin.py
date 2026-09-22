from django.contrib import admin

from .models import BusinessDay, BusinessDayException, StaffDailyCodeReceipt


@admin.register(BusinessDay)
class BusinessDayAdmin(admin.ModelAdmin):
    list_display = ('business_date', 'status', 'cutoff_hour', 'opened_by', 'opened_at', 'closed_by', 'closed_at')
    list_filter = ('status', 'business_date')
    readonly_fields = ('code_hash', 'code_ciphertext', 'code_version', 'code_issued_at', 'closing_snapshot')


@admin.register(BusinessDayException)
class BusinessDayExceptionAdmin(admin.ModelAdmin):
    list_display = ('business_day', 'category', 'severity', 'status', 'title_ar', 'resolved_by', 'resolved_at')
    list_filter = ('category', 'severity', 'status', 'business_day')
    search_fields = ('title_ar', 'fingerprint', 'object_id')


@admin.register(StaffDailyCodeReceipt)
class StaffDailyCodeReceiptAdmin(admin.ModelAdmin):
    list_display = ('business_day', 'user', 'code_version', 'notified_at', 'viewed_at', 'last_used_at', 'use_count')
    list_filter = ('business_day', 'code_version')
    search_fields = ('user__username', 'user__phone')
