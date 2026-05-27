from django.contrib import admin
from .models import Vendor, VendorParticipation

admin.site.register([Vendor, VendorParticipation])
