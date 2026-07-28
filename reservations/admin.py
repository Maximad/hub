from django.contrib import admin
from .models import Reservation

@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'reservation_type', 'effective_date_display', 'effective_time_display', 'effective_room_display', 'party_size', 'status', 'event', 'table_area')
    list_filter = ('status', 'reservation_type', 'reservation_date', 'event')
    search_fields = ('name', 'phone', 'notes')
    autocomplete_fields = ('event', 'room', 'table_area')

    @admin.display(description='التاريخ', ordering='reservation_date')
    def effective_date_display(self, obj): return obj.effective_date

    @admin.display(description='الوقت', ordering='start_time')
    def effective_time_display(self, obj): return obj.effective_starts_at

    @admin.display(description='المساحة')
    def effective_room_display(self, obj): return obj.effective_room
