from django.contrib import admin
from django.utils.html import format_html
from catalog.models import ProductMedia, ProductOptionGroupAssignment
from .settings_helpers import get_system_settings
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
        ('الهوية العامة', {
            'fields': (
                'system_title_ar', 'system_title_en', 'public_brand_title_ar', 'public_brand_title_en',
                'header_subtitle_ar', 'header_subtitle_en', 'default_language', 'custom_font_name', 'custom_font_file',
            ),
            'description': 'إعدادات الهوية التي تظهر في واجهات مشاريب العامة وواجهات الفريق.',
        }),
        ('الطلبات', {
            'fields': ('enable_table_orders', 'enable_general_in_space_orders', 'show_internal_order_uuid', 'default_order_mode'),
        }),
        ('خيارات الطلب والتوصيل', {
            'fields': (
                'enable_delivery', 'enable_takeaway', 'default_fulfillment_mode',
                'require_phone_for_delivery', 'require_address_for_delivery',
                'delivery_fee_mode', 'fixed_delivery_fee_syp', 'minimum_delivery_order_syp',
                'delivery_working_hours_text', 'delivery_contact_phone', 'delivery_contact_whatsapp', 'delivery_notes',
            ),
            'description': 'التوصيل والتيك أواي يبقيان مخفيين عن المنيو العام ونقطة البيع ما لم يتم تفعيلهما هنا. إذا كان الوضع الافتراضي غير متاح فسيعود النظام إلى طلب داخل المكان.',
        }),
        ('الألوان', {
            'fields': (
                'primary_color', 'header_color', 'background_color', 'card_background_color',
                'text_color', 'muted_text_color', 'border_color', 'button_color', 'accent_color',
            ),
            'description': 'استخدم ألوان HEX آمنة مثل #0f5f57. لا حاجة لمعرفة CSS.',
        }),
        ('الخطوط والأحجام', {
            'fields': (
                'base_font_size_px', 'heading_font_size_px', 'small_font_size_px', 'button_font_size_px',
                'button_padding_scale', 'input_size', 'border_radius_px', 'card_shadow_level',
                'page_max_width_px', 'ui_density',
            ),
            'description': 'القيم محددة بنطاقات آمنة حتى لا تنكسر الواجهة على الجوال أو التابلت.',
        }),
        ('تخطيط المنيو', {
            'fields': (
                'menu_layout_preset', 'menu_mobile_layout', 'menu_tablet_columns', 'menu_desktop_columns',
                'product_card_style', 'sticky_cart_style', 'cart_review_position',
            ),
            'description': 'تحكم بطريقة عرض المنيو العام دون تغيير حقول إرسال الطلب.',
        }),
        ('تخطيط نقطة البيع', {
            'fields': (
                'staff_home_layout', 'pos_layout_preset', 'pos_mobile_columns', 'pos_tablet_columns',
                'pos_desktop_columns', 'pos_cart_position', 'order_board_density', 'cashier_layout',
            ),
        }),
        ('عناصر الإظهار والإخفاء', {
            'fields': (
                'show_public_header', 'show_brand_name', 'show_header_subtitle', 'show_table_banner',
                'show_section_chips', 'show_product_images', 'show_product_descriptions', 'show_product_prices',
                'show_product_badges', 'show_modifier_summary', 'collapse_modifiers_by_default', 'show_item_notes',
                'show_customer_name_field', 'show_customer_phone_field', 'show_general_note_field', 'show_sticky_cart',
                'pos_show_product_images', 'pos_show_prices', 'pos_show_section_chips', 'pos_enable_search_bar',
            ),
        }),
    )
    list_display = ('system_title_ar', 'default_language', 'menu_layout_preset', 'pos_layout_preset', 'ui_density', 'updated_at')

    def has_add_permission(self, request):
        return not SystemSetting.objects.exists()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        get_system_settings.cache_clear()


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
    list_display = ('display_number', 'fulfillment_mode', 'delivery_status', 'customer_summary', 'total_amount', 'paid_amount', 'remaining_amount', 'status', 'created_at', 'public_code')
    list_filter = ('fulfillment_mode', 'delivery_status', 'status', 'created_at')
    search_fields = ('public_code', 'notes', 'table__name_ar', 'delivery_address', 'delivery_area', 'assigned_driver_name', 'assigned_driver_phone')
    readonly_fields = ('public_code', 'display_number', 'created_at', 'updated_at')
    fieldsets = (
        ('بيانات الطلب', {'fields': ('public_code', 'display_number', 'status', 'table', 'service_mode', 'fulfillment_mode', 'notes')}),
        ('بيانات التوصيل', {'fields': ('delivery_status', 'delivery_area', 'delivery_address', 'delivery_notes', 'delivery_fee_syp', 'delivery_eta_minutes', 'assigned_driver_name', 'assigned_driver_phone')}),
        ('التواريخ', {'fields': ('created_at', 'updated_at')}),
    )
    autocomplete_fields = ('table',)
    inlines = (OrderItemInline,)

    @admin.display(description='رقم العرض')
    def display_number(self, obj):
        return obj.display_number

    @admin.display(description='الزبون')
    def customer_summary(self, obj):
        lines = [line.strip() for line in (obj.notes or '').splitlines() if line.startswith('الاسم:') or line.startswith('الهاتف:')]
        return ' / '.join(lines) or '—'

    @admin.display(description='المجموع')
    def total_amount(self, obj):
        return obj.total_with_delivery_syp

    @admin.display(description='المدفوع')
    def paid_amount(self, obj):
        return sum(payment.amount_syp for payment in obj.payments.all() if payment.method != Payment.Method.UNPAID)

    @admin.display(description='المتبقي')
    def remaining_amount(self, obj):
        return max(obj.total_with_delivery_syp - self.paid_amount(obj), 0)


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
