from django.contrib import admin

from .models import (
    BusinessDay,
    BusinessDayChecklistItem,
    BusinessDayException,
    BusinessDayStaffAssignment,
    BusinessDayTask,
    HandoverNote,
    OperationalTaskTemplate,
    StaffDailyCodeReceipt,
)


@admin.register(BusinessDay)
class BusinessDayAdmin(admin.ModelAdmin):
    list_display = (
        'business_date', 'status', 'cutoff_hour', 'opened_by', 'opened_at',
        'opening_completed_at', 'closed_by', 'closed_at',
    )
    list_filter = ('status', 'business_date')
    readonly_fields = ('code_hash', 'code_ciphertext', 'code_version', 'code_issued_at', 'closing_snapshot')


@admin.register(BusinessDayStaffAssignment)
class BusinessDayStaffAssignmentAdmin(admin.ModelAdmin):
    list_display = ('business_day', 'user', 'role_snapshot', 'assigned_by', 'assigned_at', 'checked_in_at')
    list_filter = ('business_day', 'role_snapshot')
    search_fields = ('user__username', 'user__phone')


@admin.register(BusinessDayChecklistItem)
class BusinessDayChecklistItemAdmin(admin.ModelAdmin):
    list_display = ('business_day', 'stage', 'sort_order', 'label_ar', 'is_required', 'status', 'completed_by', 'completed_at')
    list_filter = ('business_day', 'stage', 'status', 'is_required')
    search_fields = ('label_ar', 'code', 'note')


@admin.register(HandoverNote)
class HandoverNoteAdmin(admin.ModelAdmin):
    list_display = ('business_day', 'priority', 'status', 'assigned_to', 'created_by', 'created_at', 'resolved_at')
    list_filter = ('priority', 'status', 'business_day')
    search_fields = ('message', 'assigned_to__username')


@admin.register(OperationalTaskTemplate)
class OperationalTaskTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'title_ar', 'due_time', 'priority', 'responsibility_role',
        'is_required', 'is_active', 'active_from', 'active_until',
    )
    list_filter = ('priority', 'is_required', 'is_active', 'responsibility_role')
    search_fields = ('title_ar', 'details')


@admin.register(BusinessDayTask)
class BusinessDayTaskAdmin(admin.ModelAdmin):
    list_display = (
        'business_day', 'kind', 'title_ar', 'priority', 'status', 'due_at',
        'assigned_to', 'responsibility_role', 'is_required', 'reminder_count',
    )
    list_filter = ('business_day', 'kind', 'priority', 'status', 'is_required', 'responsibility_role')
    search_fields = ('title_ar', 'details', 'fingerprint', 'source_type', 'source_id')
    readonly_fields = ('fingerprint', 'metadata', 'last_reminded_at', 'reminder_count')


@admin.register(BusinessDayException)
class BusinessDayExceptionAdmin(admin.ModelAdmin):
    list_display = ('business_day', 'category', 'severity', 'status', 'title_ar', 'resolved_by', 'resolved_at')
    list_filter = ('category', 'severity', 'status', 'business_day')
    search_fields = ('title_ar', 'fingerprint', 'object_id')


@admin.register(StaffDailyCodeReceipt)
class StaffDailyCodeReceiptAdmin(admin.ModelAdmin):
    list_display = (
        'business_day', 'user', 'code_version', 'notified_at', 'viewed_at',
        'last_reminded_at', 'reminder_count', 'last_used_at', 'use_count',
    )
    list_filter = ('business_day', 'code_version')
    search_fields = ('user__username', 'user__phone')
