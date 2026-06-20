from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html
from catalog.admin_media import safe_media_preview
from catalog.models import ProductMedia, ProductOptionGroupAssignment
from .settings_helpers import get_system_settings
from .stock_recipes import calculate_recipe_cost, update_product_cost_from_recipe
from .services.margins import product_unit_margin
from .models import (
    ActivityLog,
    CancellationReason,
    Category,
    InternetPackage,
    InternetSession,
    DailyClose,
    ExpenseCategory, Expense, CashMovement,
    InventoryItem, Purchase, PurchaseItem, StockMovement, ProductRecipeItem, ProductionBatch, ProductionBatchIngredient,
    Member,
    Order,
    OrderDiscount,
    OrderItem,
    PageSetting,
    Payment,
    NotificationEvent, NotificationRecipient, NotificationPreference, NotificationLog,
    Product,
    Room,
    Shift,
    SystemSetting,
    TableArea,
)


class ProductMediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1
    verbose_name = 'ربط صورة من مكتبة الوسائط'
    verbose_name_plural = 'وسائط المنتج — ارفع الصورة في مكتبة الوسائط ثم اربطها بالمنتج هنا.'
    fields = ('media_asset', 'media_type', 'url', 'alt_text_ar', 'is_primary', 'is_active', 'display_on_public_menu', 'display_on_pos', 'sort_order', 'media_preview')
    readonly_fields = ('media_preview',)

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        return safe_media_preview(obj, width=64)


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


class ProductRecipeItemInline(admin.TabularInline):
    model = ProductRecipeItem
    extra = 0
    fields = ('inventory_item', 'quantity_per_unit', 'unit', 'waste_factor_percent', 'is_active', 'notes')


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'item_type', 'unit', 'current_quantity', 'low_stock_threshold', 'estimated_unit_cost_syp', 'used_in_recipes_count', 'is_active')
    list_filter = ('item_type', 'unit', 'is_active')
    search_fields = ('name_ar', 'name_en', 'code')
    readonly_fields = ('used_in_recipes_count',)

    @admin.display(description='مستخدم في وصفات')
    def used_in_recipes_count(self, obj):
        return obj.recipe_items.count()


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1
    fields = ('inventory_item', 'quantity', 'unit', 'unit_cost_syp', 'line_total_syp', 'notes')
    readonly_fields = ('line_total_syp',)


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('business_date', 'supplier_label', 'status', 'total_syp', 'amount_paid_syp', 'remaining_display', 'paid_from', 'created_by')
    list_filter = ('business_date', 'status', 'payment_method', 'paid_from')
    search_fields = ('supplier_name', 'invoice_number', 'notes')
    inlines = (PurchaseItemInline,)

    @admin.display(description='المتبقي')
    def remaining_display(self, obj):
        return obj.remaining_syp


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('purchase', 'inventory_item', 'quantity', 'unit_cost_syp', 'line_total_syp')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('business_date', 'inventory_item', 'movement_type', 'direction', 'quantity', 'related_order', 'related_order_item', 'product', 'related_batch', 'created_by')
    list_filter = ('movement_type', 'direction', 'business_date')
    search_fields = ('inventory_item__name_ar', 'inventory_item__name_en', 'reason')


@admin.register(ProductRecipeItem)
class ProductRecipeItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'inventory_item', 'quantity_per_unit', 'unit', 'waste_factor_percent', 'is_active')
    list_filter = ('unit', 'is_active')
    search_fields = ('product__name_ar', 'inventory_item__name_ar')


class ProductionBatchIngredientInline(admin.TabularInline):
    model = ProductionBatchIngredient
    extra = 1
    fields = ('inventory_item', 'planned_quantity', 'actual_quantity', 'unit', 'estimated_unit_cost_syp_snapshot', 'estimated_line_cost_syp_snapshot', 'notes')
    readonly_fields = ('estimated_line_cost_syp_snapshot',)


@admin.register(ProductionBatch)
class ProductionBatchAdmin(admin.ModelAdmin):
    list_display = ('business_date', 'batch_name_ar', 'product', 'produced_quantity', 'status', 'prepared_by')
    list_filter = ('business_date', 'status', 'batch_type')
    search_fields = ('batch_name_ar', 'batch_name_en', 'product__name_ar', 'product__name_en')
    autocomplete_fields = ('product', 'prepared_by', 'output_inventory_item')
    inlines = (ProductionBatchIngredientInline,)


@admin.register(ProductionBatchIngredient)
class ProductionBatchIngredientAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('batch', 'inventory_item', 'planned_quantity', 'actual_quantity', 'unit', 'estimated_line_cost_syp_snapshot')
    search_fields = ('batch__batch_name_ar', 'inventory_item__name_ar')



class SystemSettingAdminForm(forms.ModelForm):
    COLOR_FIELDS = (
        'primary_color', 'header_color', 'accent_color', 'background_color', 'surface_color',
        'card_background_color', 'text_color', 'muted_text_color', 'border_color', 'button_color',
    )
    NUMBER_FIELDS = ('base_font_size_px', 'heading_font_size_px', 'small_font_size_px', 'button_font_size_px', 'border_radius_px', 'border_radius', 'page_max_width_px')

    class Meta:
        model = SystemSetting
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        errors = {}
        for field in self.COLOR_FIELDS:
            value = cleaned.get(field)
            fallback = SystemSetting.SAFE_COLOR_DEFAULTS[field]
            if value in (None, ''):
                cleaned[field] = fallback
            elif SystemSetting.safe_color_value(value, fallback) != str(value).strip():
                errors[field] = 'أدخل لوناً آمناً بصيغة HEX مثل #0f5f57 أو #fff.'
        for field in self.NUMBER_FIELDS:
            if cleaned.get(field) in (None, ''):
                continue
            fallback, minimum, maximum = SystemSetting.SAFE_NUMBER_DEFAULTS[field]
            safe_value = SystemSetting.safe_int_value(cleaned.get(field), fallback, minimum, maximum)
            if safe_value != cleaned.get(field):
                errors[field] = f'أدخل رقماً بين {minimum} و {maximum}.'
        if errors:
            raise ValidationError(errors)
        return cleaned


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    form = SystemSettingAdminForm
    fieldsets = (
        ('الهوية العامة', {
            'fields': (
                'system_title_ar', 'system_title_en', 'public_brand_title_ar', 'public_brand_title_en',
                'header_subtitle_ar', 'header_subtitle_en', 'public_menu_title', 'public_menu_subtitle', 'default_language', 'brand_logo_media', 'brand_icon_media', 'public_menu_banner_media', 'pos_logo_media', 'receipt_logo_media', 'custom_font_name', 'custom_font_file',
            ),
            'description': 'إعدادات الهوية التي تظهر في واجهات مشاريب العامة وواجهات الفريق.',
        }),
        ('الطلبات', {
            'fields': ('enable_table_orders', 'enable_general_in_space_orders', 'show_internal_order_uuid', 'default_order_mode'),
        }),
        ('خيارات الطلب والتوصيل', {
            'fields': (
                'delivery_enabled', 'takeaway_enabled', 'enable_delivery', 'enable_takeaway', 'default_fulfillment_mode',
                'require_delivery_phone', 'require_delivery_address', 'allow_unpaid_delivery', 'require_phone_for_delivery', 'require_address_for_delivery',
                'delivery_fee_mode', 'default_delivery_fee_syp', 'fixed_delivery_fee_syp', 'minimum_delivery_order_syp',
                'delivery_working_hours_text', 'delivery_contact_phone', 'delivery_contact_whatsapp', 'delivery_notes',
            ),
            'description': 'التوصيل والتيك أواي يبقيان مخفيين عن المنيو العام ونقطة البيع ما لم يتم تفعيلهما هنا. إذا كان الوضع الافتراضي غير متاح فسيعود النظام إلى طلب داخل المكان.',
        }),
        ('الألوان', {
            'fields': (
                'primary_color', 'header_color', 'accent_color', 'background_color', 'surface_color', 'card_background_color',
                'text_color', 'muted_text_color', 'border_color', 'button_color',
            ),
            'description': 'استخدم ألوان HEX آمنة مثل #0f5f57. لا حاجة لمعرفة CSS.',
        }),
        ('الخطوط والأحجام', {
            'fields': (
                'base_font_size_px', 'heading_font_size_px', 'small_font_size_px', 'button_font_size_px',
                'button_padding_scale', 'input_size', 'border_radius_px', 'border_radius', 'card_style', 'card_shadow_level',
                'page_max_width_px', 'ui_density',
            ),
            'description': 'القيم محددة بنطاقات آمنة حتى لا تنكسر الواجهة على الجوال أو التابلت.',
        }),
        ('تخطيط المنيو', {
            'fields': (
                'public_menu_layout', 'mobile_product_density', 'product_image_ratio', 'show_product_descriptions_mobile', 'show_banner_on_public_menu', 'menu_layout_preset', 'menu_mobile_layout', 'menu_tablet_columns', 'menu_desktop_columns',
                'product_card_style', 'sticky_cart_style', 'cart_review_position',
            ),
            'description': 'تحكم بطريقة عرض المنيو العام دون تغيير حقول إرسال الطلب.',
        }),
        ('تخطيط نقطة البيع', {
            'fields': (
                'staff_home_layout', 'pos_layout_preset', 'auto_deduct_inventory_on_sale', 'stock_deduction_mode', 'strict_stock_deduction', 'pos_mobile_columns', 'pos_tablet_columns',
                'pos_desktop_columns', 'pos_cart_position', 'order_board_density', 'cashier_layout',
            ),
        }),
        ('إعدادات الإنترنت والعمل', {
            'fields': (
                'internet_metered_enabled', 'default_internet_billing_mode', 'default_rate_per_hour_syp', 'default_minimum_minutes',
                'default_rounding_increment_minutes', 'default_minimum_charge_syp', 'default_free_grace_minutes', 'default_daily_cap_syp', 'allow_guest_internet_sessions',
                'allow_member_internet_sessions', 'auto_create_order_for_metered_sessions',
                'allow_unpaid_sessions', 'internet_service_product', 'default_workspace_product', 'require_phone_for_guest_session',
            ),
            'description': 'هذه الإعدادات للفوترة اليدوية فقط. التحكم الآلي بالراوتر/الشبكة/الكابتف بورتال غير مفعّل في هذه المرحلة.',
        }),
        ('عناصر الإظهار والإخفاء', {
            'fields': (
                'show_public_header', 'show_brand_name', 'show_header_subtitle', 'show_table_banner',
                'show_section_chips', 'show_product_images', 'show_product_descriptions', 'show_product_prices',
                'show_product_badges', 'show_modifier_summary', 'collapse_modifiers_by_default', 'show_item_notes',
                'show_customer_name_field', 'show_customer_phone_field', 'show_general_note_field', 'show_sticky_cart',
                'pos_show_product_images', 'pos_show_prices', 'pos_show_section_chips', 'pos_enable_search_bar', 'branding_preview',
            ),
        }),
    )
    autocomplete_fields = ('brand_logo_media', 'brand_icon_media', 'public_menu_banner_media', 'pos_logo_media', 'receipt_logo_media', 'internet_service_product', 'default_workspace_product')
    readonly_fields = ('branding_preview',)

    def branding_preview(self, obj):
        logo = obj.brand_logo_url if obj else ''
        banner = obj.public_menu_banner_url if obj else ''
        logo_html = f'<img src="{logo}" style="width:72px;height:72px;object-fit:contain;border:1px solid #ddd;border-radius:12px;background:#fff">' if logo else '<strong>لا يوجد شعار محدد</strong>'
        banner_html = f'<img src="{banner}" style="width:260px;max-width:100%;height:90px;object-fit:cover;border-radius:16px">' if banner else '<span>لا يوجد بانر محدد</span>'
        return format_html('<div style="display:grid;gap:12px;max-width:520px"><div>{}</div><div>{}</div><div style="border:1px solid {};border-radius:{}px;padding:12px;background:{};color:{}"><strong>بطاقة منتج تجريبية</strong><p>زر ولون وهوية قبل الحفظ النهائي.</p><button type="button" style="background:{};color:white;border:0;border-radius:10px;padding:8px 14px">زر تجريبي</button></div><div style="text-align:center;border:1px dashed #999;padding:10px">رأس إيصال تجريبي<br>{}</div></div>', logo_html, banner_html, obj.safe_border_color, obj.safe_border_radius_px, obj.safe_surface_color, obj.safe_text_color, obj.safe_primary_color, logo_html)
    branding_preview.short_description = 'معاينة الهوية'

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
    actions = ('update_estimated_cost_from_recipe',)
    list_display = ('name_ar', 'name_en', 'category', 'product_type', 'price_syp', 'estimated_unit_cost_syp', 'estimated_margin_display', 'estimated_margin_percent_display', 'cost_warning', 'track_margin', 'requires_preparation', 'prep_station_ref', 'visible_on_pos', 'visible_on_qr', 'orderable_on_qr', 'requires_staff_confirmation', 'vendor', 'is_available')
    list_filter = ('track_margin', 'is_available', 'category', 'menu_sections', 'visible_on_pos', 'orderable_on_pos', 'visible_on_qr', 'orderable_on_qr', 'product_type', 'requires_preparation', 'item_type', 'prep_station_ref', 'is_alcoholic', 'requires_staff_confirmation', 'beverage_type', 'food_type', 'service_type', 'vendor', 'tags')
    search_fields = ('name_ar', 'name_en', 'description_ar', 'category__name_ar', 'menu_sections__name_ar')
    autocomplete_fields = ('category', 'vendor', 'prep_station_ref', 'cost_updated_by')
    readonly_fields = ('cost_updated_at', 'recipe_cost_preview', 'recipe_cost_difference')
    inlines = (ProductMediaInline, ProductOptionGroupAssignmentInline, ProductRecipeItemInline)


    @admin.display(description='الكلفة من الوصفة')
    def recipe_cost_preview(self, obj):
        result = calculate_recipe_cost(obj)
        warnings = []
        if result['missing_cost_items']:
            warnings.append('مكوّنات بدون كلفة: ' + '، '.join(result['missing_cost_items']))
        if result['recipe_estimated_unit_cost_syp'] > obj.price_syp:
            warnings.append('تحذير: كلفة الوصفة أعلى من سعر المنتج')
        return format_html('{} ل.س{}', int(result['recipe_estimated_unit_cost_syp']), format_html('<br><strong>{}</strong>', ' | '.join(warnings)) if warnings else '')

    @admin.display(description='فرق الكلفة اليدوية والوصفة')
    def recipe_cost_difference(self, obj):
        result = calculate_recipe_cost(obj)
        if obj.estimated_unit_cost_syp is None:
            return '—'
        return int(result['recipe_estimated_unit_cost_syp']) - obj.estimated_unit_cost_syp

    @admin.action(description='تحديث الكلفة من الوصفة')
    def update_estimated_cost_from_recipe(self, request, queryset):
        for product in queryset:
            update_product_cost_from_recipe(product, request.user)
        self.message_user(request, 'تم تحديث الكلفة من الوصفة للمنتجات المحددة.')

    @admin.display(description='الهامش التقديري')
    def estimated_margin_display(self, obj):
        margin, _percent = product_unit_margin(obj)
        return 'غير محدد' if margin is None else f'{margin} ل.س'

    @admin.display(description='نسبة الهامش')
    def estimated_margin_percent_display(self, obj):
        _margin, percent = product_unit_margin(obj)
        return 'غير محدد' if percent is None else f'{percent}%'

    @admin.display(description='تحذير الكلفة')
    def cost_warning(self, obj):
        if obj.estimated_unit_cost_syp is None:
            return 'غير محدد'
        if obj.estimated_unit_cost_syp > obj.price_syp:
            return format_html('<strong style="color:#b91c1c;">تحذير: الكلفة أعلى من السعر</strong>')
        margin, percent = product_unit_margin(obj)
        if percent is not None and percent < 15:
            return format_html('<span style="color:#b45309;">هامش منخفض</span>')
        return '—'

    def save_model(self, request, obj, form, change):
        if {'estimated_unit_cost_syp', 'cost_notes', 'track_margin'} & set(form.changed_data):
            obj.cost_updated_at = timezone.now()
            obj.cost_updated_by = request.user
        super().save_model(request, obj, form, change)


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
    readonly_fields = ('stock_deducted_at', 'stock_deduction_error', 'product_name_ar_snapshot', 'unit_price_syp_snapshot', 'line_total_syp_snapshot', 'estimated_unit_cost_syp_snapshot', 'estimated_line_cost_syp_snapshot', 'estimated_line_margin_syp_snapshot')
    fields = ('product', 'quantity', 'prep_station', 'prep_status', 'stock_deducted', 'stock_deducted_at', 'stock_deduction_error', 'product_name_ar_snapshot', 'unit_price_syp_snapshot', 'line_total_syp_snapshot', 'estimated_unit_cost_syp_snapshot', 'estimated_line_cost_syp_snapshot', 'estimated_line_margin_syp_snapshot', 'item_note')
    autocomplete_fields = ('product',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('display_number', 'fulfillment_mode', 'delivery_status', 'customer_summary', 'total_amount', 'discount_amount', 'paid_amount', 'remaining_amount', 'status', 'created_at', 'public_code')
    list_filter = ('fulfillment_mode', 'delivery_status', 'status', 'created_at')
    search_fields = ('public_code', 'notes', 'table__name_ar', 'delivery_phone', 'delivery_address', 'delivery_area', 'assigned_driver_name', 'assigned_driver_phone')
    readonly_fields = ('public_code', 'display_number', 'created_at', 'updated_at')
    fieldsets = (
        ('بيانات الطلب', {'fields': ('public_code', 'display_number', 'status', 'table', 'service_mode', 'fulfillment_mode', 'notes')}),
        ('بيانات التوصيل', {'fields': ('delivery_status', 'delivery_customer_name', 'delivery_phone', 'delivery_area', 'delivery_address', 'delivery_landmark', 'delivery_notes', 'delivery_fee_syp', 'delivery_eta_minutes', 'assigned_to', 'assigned_driver_name', 'assigned_driver_phone', 'delivery_created_at', 'delivery_confirmed_at', 'delivery_out_at', 'delivery_delivered_at', 'delivery_cancelled_at')}),
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
        return obj.total_syp

    @admin.display(description='الخصم')
    def discount_amount(self, obj):
        return obj.discount_syp

    @admin.display(description='المدفوع')
    def paid_amount(self, obj):
        return obj.paid_syp

    @admin.display(description='المتبقي')
    def remaining_amount(self, obj):
        return obj.remaining_syp


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('order', 'product_name_ar_snapshot', 'quantity', 'prep_station', 'prep_status', 'stock_deducted', 'unit_price_syp_snapshot', 'line_total_syp_snapshot', 'estimated_unit_cost_syp_snapshot', 'estimated_line_margin_syp_snapshot')
    search_fields = ('order__public_code', 'product_name_ar_snapshot', 'product__name_ar')
    list_filter = ('prep_status', 'prep_station')
    autocomplete_fields = ('order', 'product')


@admin.register(OrderDiscount)
class OrderDiscountAdmin(admin.ModelAdmin):
    list_display = ('order', 'amount_syp', 'discount_type', 'reason', 'created_by', 'approved_by', 'created_at', 'is_active')
    list_filter = ('discount_type', 'created_at', 'is_active', 'created_by', 'approved_by')
    search_fields = ('order__public_code', 'order__id', 'reason', 'notes', 'created_by__username', 'approved_by__username')
    autocomplete_fields = ('order', 'created_by', 'approved_by')


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'code', 'category_type', 'is_active', 'sort_order')
    list_filter = ('category_type', 'is_active')
    search_fields = ('name_ar', 'name_en', 'code')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('business_date', 'title', 'category', 'amount_syp', 'payment_method', 'paid_from', 'status', 'vendor_supplier', 'created_by')
    list_filter = ('business_date', 'category', 'payment_method', 'paid_from', 'status')
    search_fields = ('title', 'description', 'supplier_name', 'receipt_number')
    autocomplete_fields = ('category', 'vendor', 'receipt_media', 'created_by', 'approved_by', 'paid_by')
    @admin.display(description='البائع/المورد')
    def vendor_supplier(self, obj):
        return obj.supplier_label


@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = ('business_date', 'movement_type', 'direction', 'amount_syp', 'title', 'created_by', 'created_at')
    list_filter = ('business_date', 'movement_type', 'direction', 'is_cancelled')
    search_fields = ('title', 'notes')
    autocomplete_fields = ('related_expense', 'related_order', 'related_payment', 'vendor', 'created_by', 'approved_by')


@admin.register(DailyClose)
class DailyCloseAdmin(admin.ModelAdmin):
    list_display = ('business_date', 'expected_cash_syp', 'actual_cash_counted_syp', 'cash_difference_syp', 'closed_by', 'closed_at', 'is_finalized')
    list_filter = ('business_date', 'is_finalized', 'closed_by')
    search_fields = ('business_date', 'notes', 'closed_by__username')
    autocomplete_fields = ('closed_by',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'amount_syp', 'method', 'created_by', 'is_active', 'is_reversed', 'created_at')
    list_filter = ('method', 'is_active', 'is_reversed', 'created_at')
    search_fields = ('order__public_code', 'notes', 'created_by__username')
    autocomplete_fields = ('order', 'created_by', 'reversed_by')


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
    list_display = ('id', 'customer_label', 'session_type', 'billing_mode', 'status', 'started_at', 'ended_at', 'duration_minutes', 'billable_minutes', 'calculated_total_syp', 'linked_order')
    list_filter = ('session_type', 'billing_mode', 'status', 'network_provider', 'started_at')
    search_fields = ('guest_name', 'guest_phone', 'customer_name', 'customer_phone', 'member__name_ar', 'member__name_en', 'member__phone', 'access_code', 'network_session_id', 'linked_order__display_number')
    autocomplete_fields = ('member', 'package', 'linked_order', 'linked_payment', 'started_by', 'ended_by')
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='العضو/الزائر')
    def customer_label(self, obj):
        if obj.member_id:
            return obj.member
        return obj.display_guest_name or obj.display_guest_phone or 'زائر'


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('opened_by', 'opened_at', 'closed_by', 'closed_at')
    search_fields = ('opened_by__username', 'closed_by__username')


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('created_at', 'actor', 'action', 'details')
    list_filter = ('action', 'created_at')
    search_fields = ('actor__username', 'action', 'details')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'title_ar', 'order', 'order_item', 'target_station', 'target_role', 'created_at', 'is_active')
    list_filter = ('event_type', 'target_station', 'target_role', 'is_active', 'created_at')
    search_fields = ('title_ar', 'message_ar', 'order__public_code', 'order__id')
    autocomplete_fields = ('order', 'order_item', 'target_station', 'created_by')


@admin.register(NotificationRecipient)
class NotificationRecipientAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('notification_event', 'user', 'role', 'station', 'is_read', 'delivered_at', 'read_at')
    list_filter = ('role', 'station', 'is_read')
    autocomplete_fields = ('notification_event', 'user', 'station')


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        return {}

    list_display = ('notification_event', 'channel', 'status', 'recipient_user', 'recipient_role', 'recipient_station', 'created_at', 'sent_at')
    list_filter = ('channel', 'status', 'created_at')
    autocomplete_fields = ('notification_event', 'recipient_user', 'recipient_station')


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'enable_sound', 'enable_browser_notifications', 'updated_at')
    autocomplete_fields = ('user',)
