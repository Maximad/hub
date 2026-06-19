from django.contrib import admin
from catalog.admin_media import safe_media_preview
from .models import Event, EventMedia, EventTicketType


class EventMediaInline(admin.TabularInline):
    model = EventMedia
    extra = 1
    fields = ('media_asset', 'role', 'is_primary', 'is_active', 'sort_order', 'media_preview')
    readonly_fields = ('media_preview',)
    autocomplete_fields = ('media_asset',)

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        return safe_media_preview(obj, width=64, ratio='4 / 5')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title_ar', 'starts_at', 'ends_at', 'location_area', 'capacity', 'status', 'is_public')
    list_filter = ('status', 'is_public', 'location_area')
    search_fields = ('title_ar', 'title_en', 'description_ar')
    inlines = (EventMediaInline,)


@admin.register(EventMedia)
class EventMediaAdmin(admin.ModelAdmin):
    list_display = ('event', 'role', 'is_primary', 'is_active', 'sort_order', 'media_preview', 'updated_at')
    list_filter = ('role', 'is_primary', 'is_active')
    search_fields = ('event__title_ar', 'event__title_en', 'media_asset__title_ar', 'media_asset__title_en')
    autocomplete_fields = ('event', 'media_asset')
    readonly_fields = ('media_preview', 'created_at', 'updated_at')

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        return safe_media_preview(obj, ratio='4 / 5')


@admin.register(EventTicketType)
class EventTicketTypeAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'event', 'price_syp', 'capacity', 'is_active')
    list_filter = ('is_active', 'event')
    search_fields = ('name_ar',)
