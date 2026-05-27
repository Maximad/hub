from django.contrib import admin
from django.utils.html import format_html
from .models import Room, TableArea, Category, Product, Order, OrderItem, Payment, Member, InternetPackage, InternetSession, Shift, ActivityLog


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'item_type', 'beverage_type', 'food_type', 'service_type', 'price_syp', 'is_alcoholic', 'visible_on_qr', 'orderable_on_qr', 'requires_staff_confirmation', 'vendor', 'is_available')
    list_filter = ('is_available', 'visible_on_qr', 'orderable_on_qr', 'item_type', 'is_alcoholic', 'requires_staff_confirmation', 'beverage_type', 'food_type', 'service_type', 'vendor', 'menu_sections', 'tags')
    search_fields = ('name_ar', 'name_en', 'description_ar')


@admin.register(TableArea)
class TableAreaAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'room', 'qr_token', 'qr_menu_link')
    readonly_fields = ('qr_token', 'qr_menu_link')

    @admin.display(description='رابط منيو QR')
    def qr_menu_link(self, obj):
        return format_html('<a href="/menu/table/{}/" target="_blank">/menu/table/{}/</a>', obj.qr_token, obj.qr_token)


admin.site.register([Room, Category, Order, OrderItem, Payment, Member, InternetPackage, InternetSession, Shift, ActivityLog])
