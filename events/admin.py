from django.contrib import admin
from .models import Event, EventTicketType

admin.site.register([Event, EventTicketType])
