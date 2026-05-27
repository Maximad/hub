from django.contrib import admin
from .models import Reservation

@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'reservation_type', 'reservation_date', 'start_time', 'party_size', 'status', 'event', 'table_area')
    list_filter = ('status', 'reservation_type', 'reservation_date', 'event')
    search_fields = ('name', 'phone', 'notes')
