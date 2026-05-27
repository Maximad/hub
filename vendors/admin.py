from django.contrib import admin
from .models import Vendor, VendorParticipation

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'vendor_type', 'contact_person', 'phone', 'is_active')
    list_filter = ('vendor_type', 'is_active')
    search_fields = ('name_ar', 'name_en', 'contact_person', 'phone', 'settlement_notes')


@admin.register(VendorParticipation)
class VendorParticipationAdmin(admin.ModelAdmin):
    list_display = ('title_ar', 'vendor', 'starts_at', 'ends_at', 'location_area', 'status')
    list_filter = ('status', 'starts_at', 'vendor')
    search_fields = ('title_ar', 'notes', 'vendor__name_ar')
