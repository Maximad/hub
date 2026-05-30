from django.contrib import admin
from django.utils.html import format_html
from catalog.models import ProductMedia, ProductOptionGroupAssignment
from .models import (
    ActivityLog,
    Category,
    InternetPackage,
    InternetSession,
    Member,
    Order,
    OrderItem,
    PageSetting,
    Payment,
    Product,
    Room,
    Shift,
    SystemSetting,
    TableArea,
)


class ProductMediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1
    fields = ('media_type', 'url', 'alt_text_ar', 'is_primary', 'is_active', 'sort_order', 'media_preview')
    readonly_fields = ('media_preview',)

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        if obj and obj.url and obj.media_type in {ProductMedia.MediaType.IMAGE, ProductMedia.MediaType.GIF}:
            return format_html('<img src="{}" alt="{}" style="max-width: 80px; max-height: 60px; border-radius: 8px; object-fit: cover;" />', obj.url, obj.display_alt_text)
        return '—'


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


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    fieldsets = (
        ('العناوين واللغة', {'fields': ('system_title_ar', 'system_title_en', 'public_brand_title_ar', 'public_brand_title_en', 'header_subtitle_ar', 'header_subtitle_en', 'default_language')}),
        ('الطلبات', {'fields': ('enable_takeaway', 'enable_table_orders', 'enable_general_in_space_orders', 'show_internal_order_uuid', 'default_order_mode')}),
        ('الثيم والألوان', {'fields': ('primary_color', 'header_color', 'button_color', 'accent_color', 'background_color', 'card_background_color', 'text_color', 'border_color', 'base_font_size_px', 'heading_font_size_px', 'border_radius_px')}),
        ('الخط', {'fields': ('custom_font_name', 'custom_font_file')}),
    )
    list_display = ('system_title_ar', 'default_language', 'enable_takeaway', 'updated_at')

    def has_add_permission(self, request):
        return not SystemSetting.objects.exists()


@admin.register(PageSetting)
class PageSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'title_ar', 'title_en', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('key', 'title_ar', 'title_en', 'subtitle_ar', 'subtitle_en')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'name_en', 'category', 'item_type', 'price_syp', 'visible_on_qr', 'orderable_on_qr', 'requires_staff_confirmation', 'vendor', 'is_available')
    list_filter = ('is_available', 'visible_on_qr', 'orderable_on_qr', 'item_type', 'is_alcoholic', 'requires_staff_confirmation', 'beverage_type', 'food_type', 'service_type', 'vendor', 'menu_sections', 'tags')
    search_fields = ('name_ar', 'name_en', 'description_ar', 'category__name_ar')
    autocomplete_fields = ('category', 'vendor')
    inlines = (ProductMediaInline, ProductOptionGroupAssignmentInline)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'name_en', 'updated_at')
    search_fields = ('name_ar', 'name_en', 'description_ar', 'description_en')


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'name_en', 'updated_at')
    search_fields = ('name_ar', 'name_en')


@admin.register(TableArea)
class TableAreaAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'room', 'qr_token', 'qr_menu_link')
    list_filter = ('room',)
    search_fields = ('name_ar', 'name_en', 'room__name_ar')
    readonly_fields = ('qr_token', 'qr_menu_link')
    autocomplete_fields = ('room',)

    @admin.display(description='رابط منيو QR')
    def qr_menu_link(self, obj):
        return format_html('<a href="/menu/table/{}/" target="_blank">/menu/table/{}/</a>', obj.qr_token, obj.qr_token)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product_name_ar_snapshot', 'unit_price_syp_snapshot', 'line_total_syp_snapshot')
    autocomplete_fields = ('product',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('display_number', 'status', 'service_mode', 'table', 'created_at', 'public_code')
    list_filter = ('status', 'service_mode', 'table')
    search_fields = ('public_code', 'notes', 'table__name_ar')
    readonly_fields = ('public_code', 'display_number', 'created_at', 'updated_at')
    autocomplete_fields = ('table',)
    inlines = (OrderItemInline,)

    @admin.display(description='رقم العرض')
    def display_number(self, obj):
        return obj.display_number


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product_name_ar_snapshot', 'quantity', 'unit_price_syp_snapshot', 'line_total_syp_snapshot')
    search_fields = ('order__public_code', 'product_name_ar_snapshot', 'product__name_ar')
    autocomplete_fields = ('order', 'product')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'amount_syp', 'method', 'created_at')
    list_filter = ('method',)
    search_fields = ('order__public_code',)
    autocomplete_fields = ('order',)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'phone', 'balance_syp', 'default_plan', 'created_at')
    list_filter = ('default_plan',)
    search_fields = ('name_ar', 'name_en', 'phone')
    autocomplete_fields = ('default_plan',)


@admin.register(InternetPackage)
class InternetPackageAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'duration_minutes', 'price_syp', 'updated_at')
    search_fields = ('name_ar', 'name_en', 'description_ar')


@admin.register(InternetSession)
class InternetSessionAdmin(admin.ModelAdmin):
    list_display = ('member', 'customer_name', 'package', 'start_time', 'end_time', 'actual_duration_minutes', 'status')
    list_filter = ('status', 'package')
    search_fields = ('member__name_ar', 'member__phone', 'customer_name', 'customer_phone')
    autocomplete_fields = ('member', 'package')


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('opened_by', 'opened_at', 'closed_by', 'closed_at')
    search_fields = ('opened_by__username', 'closed_by__username')


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'details')
    list_filter = ('action', 'created_at')
    search_fields = ('actor__username', 'action', 'details')
    readonly_fields = ('created_at', 'updated_at')
