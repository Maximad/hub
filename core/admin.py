from django.contrib import admin
from .models import Room, TableArea, Category, Product, Order, OrderItem, Payment, Member, InternetPackage, InternetSession, Shift, ActivityLog


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'item_type', 'beverage_type', 'food_type', 'service_type', 'price_syp', 'is_alcoholic', 'visible_on_qr', 'orderable_on_qr', 'requires_staff_confirmation', 'vendor', 'is_available')
    list_filter = ('item_type', 'beverage_type', 'food_type', 'service_type', 'is_alcoholic', 'vendor', 'is_available', 'menu_sections', 'tags')
    search_fields = ('name_ar', 'name_en', 'description_ar')


admin.site.register([Room, TableArea, Category, Order, OrderItem, Payment, Member, InternetPackage, InternetSession, Shift, ActivityLog])
