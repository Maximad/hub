from django.contrib import admin
from django.utils.html import format_html
from catalog.models import ProductOptionGroupAssignment
from .models import Room, TableArea, Category, Product, Order, OrderItem, Payment, Member, InternetPackage, InternetSession, Shift, ActivityLog


class ProductOptionGroupAssignmentInline(admin.TabularInline):
    model = ProductOptionGroupAssignment
    extra = 1
    fields = ('group', 'applicability_hint', 'is_active', 'sort_order')
    readonly_fields = ('applicability_hint',)

    @admin.display(description='ملخص الانطباق')
    def applicability_hint(self, obj):
        if not obj or not obj.group_id:
            return 'اختر مجموعة لعرض الانطباق.'
        return obj.group.applicability_summary


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'item_type', 'beverage_type', 'food_type', 'service_type', 'price_syp', 'is_alcoholic', 'visible_on_qr', 'orderable_on_qr', 'requires_staff_confirmation', 'vendor', 'is_available')
    list_filter = ('is_available', 'visible_on_qr', 'orderable_on_qr', 'item_type', 'is_alcoholic', 'requires_staff_confirmation', 'beverage_type', 'food_type', 'service_type', 'vendor', 'menu_sections', 'tags')
    search_fields = ('name_ar', 'name_en', 'description_ar')
    inlines = (ProductOptionGroupAssignmentInline,)


@admin.register(TableArea)
class TableAreaAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'room', 'qr_token', 'qr_menu_link')
    readonly_fields = ('qr_token', 'qr_menu_link')

    @admin.display(description='رابط منيو QR')
    def qr_menu_link(self, obj):
        return format_html('<a href="/menu/table/{}/" target="_blank">/menu/table/{}/</a>', obj.qr_token, obj.qr_token)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'phone', 'balance_syp', 'default_plan', 'created_at')
    list_filter = ('default_plan',)
    search_fields = ('name_ar', 'name_en', 'phone')


@admin.register(InternetSession)
class InternetSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'member', 'package', 'start_time', 'end_time', 'actual_duration_minutes', 'status')
    list_filter = ('status', 'package')
    search_fields = ('member__name_ar', 'customer_name', 'customer_phone')


admin.site.register([Room, Category, Order, OrderItem, Payment, InternetPackage, Shift, ActivityLog])
