from django.contrib import admin
from django.utils.html import format_html
from .models import (
    MenuSection,
    PrepStation,
    ProductAvailability,
    ProductOption,
    ProductOptionGroup,
    ProductOptionGroupAssignment,
    ProductMedia,
    Tag,
)


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


@admin.register(ProductMedia)
class ProductMediaAdmin(admin.ModelAdmin):
    list_display = ('readable_product_name', 'media_type', 'is_primary', 'is_active', 'sort_order', 'media_preview', 'updated_at')
    list_filter = ('media_type', 'is_primary', 'is_active')
    search_fields = ('product__name_ar', 'product__name_en', 'alt_text_ar', 'url')
    ordering = ('product__name_ar', 'sort_order', '-is_primary')
    autocomplete_fields = ('product',)
    readonly_fields = ('media_preview', 'uuid', 'created_at', 'updated_at')
    fields = ('uuid', 'product', 'media_type', 'url', 'alt_text_ar', 'is_primary', 'is_active', 'sort_order', 'media_preview', 'created_at', 'updated_at')

    @admin.display(description='المنتج', ordering='product__name_ar')
    def readable_product_name(self, obj):
        return obj.product.name_ar

    @admin.display(description='معاينة')
    def media_preview(self, obj):
        if obj and obj.url and obj.media_type in {ProductMedia.MediaType.IMAGE, ProductMedia.MediaType.GIF}:
            return format_html('<span style="display: inline-flex; align-items: center; justify-content: center; width: 72px; aspect-ratio: 2 / 3; overflow: hidden; border-radius: 8px; background: #f8f5ef;"><img src="{}" alt="{}" style="width: 100%; height: 100%; object-fit: cover; object-position: center center; display: block;" /></span>', obj.url, obj.display_alt_text)
        return '—'


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
    list_display = ('name_ar', 'name_en', 'is_active', 'sort_order')
    list_filter = ('is_active',)
    search_fields = ('name_ar', 'name_en', 'code')
    ordering = ('sort_order', 'name_ar')


admin.site.register([MenuSection, Tag, ProductAvailability])
