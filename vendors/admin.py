from django.contrib import admin
from catalog.admin_media import safe_media_preview
from .models import Vendor, VendorMedia, VendorParticipation


class VendorMediaInline(admin.TabularInline):
    model = VendorMedia
    extra = 1
    fields = ('media_asset', 'role', 'is_primary', 'is_active', 'sort_order', 'media_preview')
    readonly_fields = ('media_preview',)
    autocomplete_fields = ('media_asset',)

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        return safe_media_preview(obj, width=64, ratio='1 / 1' if obj and obj.role == VendorMedia.Role.LOGO else '16 / 9')


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'vendor_type', 'contact_person', 'phone', 'is_active')
    list_filter = ('vendor_type', 'is_active')
    search_fields = ('name_ar', 'name_en', 'contact_person', 'phone', 'settlement_notes')
    inlines = (VendorMediaInline,)


@admin.register(VendorMedia)
class VendorMediaAdmin(admin.ModelAdmin):
    list_display = ('vendor', 'role', 'is_primary', 'is_active', 'sort_order', 'media_preview', 'updated_at')
    list_filter = ('role', 'is_primary', 'is_active')
    search_fields = ('vendor__name_ar', 'vendor__name_en', 'media_asset__title_ar', 'media_asset__title_en')
    autocomplete_fields = ('vendor', 'media_asset')
    readonly_fields = ('media_preview', 'created_at', 'updated_at')

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        return safe_media_preview(obj, ratio='1 / 1' if obj.role == VendorMedia.Role.LOGO else '16 / 9')


@admin.register(VendorParticipation)
class VendorParticipationAdmin(admin.ModelAdmin):
    list_display = ('title_ar', 'vendor', 'starts_at', 'ends_at', 'location_area', 'status')
    list_filter = ('status', 'starts_at', 'vendor')
    search_fields = ('title_ar', 'notes', 'vendor__name_ar')
