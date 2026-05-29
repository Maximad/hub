from django.contrib import admin
from .models import (
    MenuSection,
    PrepStation,
    ProductAvailability,
    ProductOption,
    ProductOptionGroup,
    ProductOptionGroupAssignment,
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


admin.site.register([MenuSection, Tag, PrepStation, ProductAvailability])
