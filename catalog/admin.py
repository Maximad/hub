from django.contrib import admin
from .models import (
    MenuSection,
    PrepStation,
    ProductAvailability,
    ProductOption,
    ProductOptionGroup,
    ProductOptionGroupAssignment,
    MediaAsset,
    ProductMedia,
    Tag,
)
from .admin_media import safe_media_preview


class ProductOptionInline(admin.TabularInline):
    model = ProductOption
    extra = 1
    fields = ('code', 'name_ar', 'name_en', 'price_delta_syp', 'is_default', 'is_active', 'sort_order')


@admin.register(ProductOptionGroup)
class ProductOptionGroupAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'name_ar',
        'selection_type',
        'is_required',
        'is_active',
        'sort_order',
        'applies_to_item_type',
        'applies_to_beverage_type',
        'applies_to_food_type',
        'applies_to_service_type',
    )
    list_filter = (
        'selection_type',
        'is_required',
        'is_active',
        'applies_to_item_type',
        'applies_to_beverage_type',
        'applies_to_food_type',
        'applies_to_service_type',
    )
    search_fields = ('code', 'name_ar', 'name_en')
    ordering = ('sort_order', 'name_ar')
    inlines = (ProductOptionInline,)


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ('thumbnail_preview', 'title_ar', 'title_en', 'media_type', 'is_active', 'uploaded_by', 'created_at', 'updated_at')
    list_filter = ('media_type', 'is_active', 'created_at')
    search_fields = ('title_ar', 'title_en', 'alt_text_ar', 'caption_ar', 'file', 'external_url')
    readonly_fields = ('thumbnail_preview', 'file_link', 'uuid', 'created_at', 'updated_at')
    fields = ('uuid', 'title_ar', 'title_en', 'file', 'external_url', 'file_link', 'media_type', 'alt_text_ar', 'alt_text_en', 'caption_ar', 'caption_en', 'is_active', 'uploaded_by', 'thumbnail_preview', 'created_at', 'updated_at')
    autocomplete_fields = ('uploaded_by',)
    actions = ('mark_active', 'mark_inactive')

    def save_model(self, request, obj, form, change):
        if not obj.uploaded_by_id and request.user.is_authenticated:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='معاينة')
    def thumbnail_preview(self, obj):
        return safe_media_preview(obj)

    @admin.display(description='رابط الملف')
    def file_link(self, obj):
        return safe_media_preview(obj, width=48)

    @admin.action(description='تعليم كوسائط نشطة')
    def mark_active(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description='تعليم كوسائط غير نشطة')
    def mark_inactive(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(ProductMedia)
class ProductMediaAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        # Product media is managed from the Product admin inline to avoid a second image-library entry.
        return {}

    list_display = ('readable_product_name', 'media_type', 'media_asset', 'is_primary', 'is_active', 'display_on_public_menu', 'display_on_pos', 'sort_order', 'media_preview', 'updated_at')
    list_filter = ('media_type', 'is_primary', 'is_active', 'display_on_public_menu', 'display_on_pos')
    search_fields = ('product__name_ar', 'product__name_en', 'alt_text_ar', 'url', 'media_asset__title_ar', 'media_asset__title_en')
    ordering = ('product__name_ar', 'sort_order', '-is_primary')
    autocomplete_fields = ('product', 'media_asset')
    readonly_fields = ('media_preview', 'uuid', 'created_at', 'updated_at')
    fields = ('uuid', 'product', 'media_asset', 'media_type', 'url', 'alt_text_ar', 'is_primary', 'is_active', 'display_on_public_menu', 'display_on_pos', 'sort_order', 'media_preview', 'created_at', 'updated_at')

    @admin.display(description='المنتج', ordering='product__name_ar')
    def readable_product_name(self, obj):
        return obj.product.name_ar

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        return safe_media_preview(obj)


@admin.register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'readable_group_name', 'price_delta_syp', 'is_default', 'is_active', 'sort_order')
    list_filter = ('group', 'is_default', 'is_active')
    search_fields = ('code', 'name_ar', 'name_en', 'group__name_ar', 'group__code')
    ordering = ('group__sort_order', 'sort_order', 'name_ar')

    @admin.display(description='مجموعة الخيارات', ordering='group__name_ar')
    def readable_group_name(self, obj):
        return obj.group.name_ar


@admin.register(ProductOptionGroupAssignment)
class ProductOptionGroupAssignmentAdmin(admin.ModelAdmin):
    def get_model_perms(self, request):
        # Keep registered for autocomplete/direct links, but avoid promoting this join table on admin home.
        return {}

    list_display = (
        'readable_product_name',
        'readable_group_name',
        'is_active',
        'sort_order',
        'group_item_type',
        'group_beverage_type',
        'group_food_type',
        'group_service_type',
        'is_applicable',
    )
    list_filter = (
        'is_active',
        'group',
        'group__applies_to_item_type',
        'group__applies_to_beverage_type',
        'group__applies_to_food_type',
        'group__applies_to_service_type',
    )
    search_fields = ('product__name_ar', 'product__name_en', 'group__name_ar', 'group__code')
    ordering = ('product__name_ar', 'sort_order')
    autocomplete_fields = ('product', 'group')

    @admin.display(description='المنتج', ordering='product__name_ar')
    def readable_product_name(self, obj):
        return obj.product.name_ar

    @admin.display(description='مجموعة الخيارات', ordering='group__name_ar')
    def readable_group_name(self, obj):
        return obj.group.name_ar

    @admin.display(description='نوع المنتج')
    def group_item_type(self, obj):
        return obj.group.applies_to_item_type or 'بدون تقييد'

    @admin.display(description='نوع المشروب')
    def group_beverage_type(self, obj):
        return obj.group.applies_to_beverage_type or 'بدون تقييد'

    @admin.display(description='نوع الطعام')
    def group_food_type(self, obj):
        return obj.group.applies_to_food_type or 'بدون تقييد'

    @admin.display(description='نوع الخدمة')
    def group_service_type(self, obj):
        return obj.group.applies_to_service_type or 'بدون تقييد'

    @admin.display(boolean=True, description='ينطبق')
    def is_applicable(self, obj):
        return obj.group.applies_to_product(obj.product)



@admin.register(PrepStation)
class PrepStationAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'code', 'station_type', 'is_active', 'sort_order')
    list_filter = ('station_type', 'is_active')
    search_fields = ('name_ar', 'name_en', 'code')
    ordering = ('sort_order', 'name_ar')


admin.site.register([MenuSection, Tag, ProductAvailability])
