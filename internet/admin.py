from django.contrib import admin

from internet.guest_wifi import current_venue_code
from .models import GuestWifiGrant, GuestWifiPolicy, WifiNetwork


@admin.register(WifiNetwork)
class WifiNetworkAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'ssid', 'is_active', 'visible_on_qr', 'show_password_on_qr', 'updated_at')
    list_filter = ('is_active', 'visible_on_qr', 'show_password_on_qr')
    search_fields = ('name_ar', 'ssid', 'notes')


@admin.register(GuestWifiPolicy)
class GuestWifiPolicyAdmin(admin.ModelAdmin):
    list_display = (
        'key', 'enabled', 'session_minutes', 'max_sessions_per_day',
        'code_rotation_minutes', 'bandwidth_profile', 'venue_code', 'updated_at',
    )
    list_filter = ('enabled', 'require_venue_code', 'member_bypass_venue_code')
    readonly_fields = ('venue_code', 'created_at', 'updated_at')
    fields = (
        'key', 'enabled', 'require_venue_code', 'member_bypass_venue_code',
        'session_minutes', 'max_sessions_per_day', 'code_rotation_minutes',
        'bandwidth_profile', 'venue_code', 'created_at', 'updated_at',
    )

    @admin.display(description='رمز المكان الحالي')
    def venue_code(self, obj):
        if not obj or not obj.enabled or not obj.require_venue_code:
            return '—'
        return current_venue_code(obj)


@admin.register(GuestWifiGrant)
class GuestWifiGrantAdmin(admin.ModelAdmin):
    list_display = ('session', 'credential', 'business_date', 'created_at')
    list_filter = ('business_date',)
    readonly_fields = ('credential', 'session', 'business_date', 'code_slot', 'created_at')
    search_fields = ('session__public_code',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
