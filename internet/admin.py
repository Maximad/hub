from django.contrib import admin

from internet.guest_wifi import current_venue_code
from .models import (
    GuestWifiDailyAllowance,
    GuestWifiGrant,
    GuestWifiOrderBonus,
    GuestWifiPolicy,
    WifiNetwork,
)


@admin.register(WifiNetwork)
class WifiNetworkAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'ssid', 'is_active', 'visible_on_qr', 'show_password_on_qr', 'updated_at')
    list_filter = ('is_active', 'visible_on_qr', 'show_password_on_qr')
    search_fields = ('name_ar', 'ssid', 'notes')


@admin.register(GuestWifiPolicy)
class GuestWifiPolicyAdmin(admin.ModelAdmin):
    list_display = (
        'key', 'enabled', 'session_minutes', 'daily_complimentary_minutes',
        'order_bonus_minutes', 'qualifying_order_minimum_syp',
        'bandwidth_profile', 'venue_code', 'updated_at',
    )
    list_filter = ('enabled', 'order_bonus_enabled', 'require_venue_code', 'member_bypass_venue_code')
    readonly_fields = ('venue_code', 'created_at', 'updated_at')
    fields = (
        'key', 'enabled', 'bandwidth_profile',
        'session_minutes', 'daily_complimentary_minutes',
        'order_bonus_enabled', 'order_bonus_minutes', 'qualifying_order_minimum_syp',
        'require_venue_code', 'code_rotation_minutes', 'member_bypass_venue_code',
        'venue_code', 'created_at', 'updated_at',
    )

    @admin.display(description='رمز المكان الحالي')
    def venue_code(self, obj):
        if not obj or not obj.enabled or not obj.require_venue_code:
            return '—'
        return current_venue_code(obj)


class ReadOnlyInternetAuditAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GuestWifiDailyAllowance)
class GuestWifiDailyAllowanceAdmin(ReadOnlyInternetAuditAdmin):
    list_display = (
        'credential', 'business_date', 'initial_minutes_granted',
        'order_bonus_minutes_granted', 'manual_bonus_minutes_granted',
        'consumed_seconds', 'updated_at',
    )
    list_filter = ('business_date',)
    readonly_fields = (
        'credential', 'business_date', 'initial_minutes_granted',
        'order_bonus_minutes_granted', 'manual_bonus_minutes_granted',
        'consumed_seconds', 'created_at', 'updated_at',
    )


@admin.register(GuestWifiGrant)
class GuestWifiGrantAdmin(ReadOnlyInternetAuditAdmin):
    list_display = ('session', 'credential', 'business_date', 'allowance', 'usage_accounted_at', 'created_at')
    list_filter = ('business_date',)
    readonly_fields = (
        'credential', 'session', 'allowance', 'business_date',
        'code_slot', 'usage_accounted_at', 'created_at',
    )
    search_fields = ('session__public_code',)


@admin.register(GuestWifiOrderBonus)
class GuestWifiOrderBonusAdmin(ReadOnlyInternetAuditAdmin):
    list_display = (
        'order', 'credential', 'business_date', 'minutes',
        'order_total_syp_snapshot', 'applied_at', 'revoked_at',
    )
    list_filter = ('business_date', 'applied_at', 'revoked_at')
    readonly_fields = (
        'order', 'allowance', 'credential', 'business_date', 'minutes',
        'order_total_syp_snapshot', 'applied_session', 'applied_at',
        'revoked_at', 'created_at', 'updated_at',
    )
