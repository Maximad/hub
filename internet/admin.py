from django.contrib import admin
from .models import WifiNetwork


@admin.register(WifiNetwork)
class WifiNetworkAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'ssid', 'is_active', 'visible_on_qr', 'show_password_on_qr', 'updated_at')
    list_filter = ('is_active', 'visible_on_qr', 'show_password_on_qr')
    search_fields = ('name_ar', 'ssid', 'notes')
