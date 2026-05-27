from django.contrib import admin
from .models import Event, EventTicketType

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title_ar', 'starts_at', 'ends_at', 'location_area', 'capacity', 'status', 'is_public')
    list_filter = ('status', 'is_public', 'location_area')
    search_fields = ('title_ar', 'title_en', 'description_ar')


@admin.register(EventTicketType)
class EventTicketTypeAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'event', 'price_syp', 'capacity', 'is_active')
    list_filter = ('is_active', 'event')
    search_fields = ('name_ar',)
