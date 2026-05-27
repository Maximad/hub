from django.contrib import admin
from .models import MenuSection, Tag, PrepStation, ProductAvailability

admin.site.register([MenuSection, Tag, PrepStation, ProductAvailability])
