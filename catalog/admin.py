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
    list_display = ('code', 'name_ar', 'selection_type', 'is_required', 'is_active', 'sort_order')
    list_filter = ('selection_type', 'is_required', 'is_active')
    search_fields = ('code', 'name_ar', 'name_en')
    ordering = ('sort_order', 'name_ar')
    inlines = (ProductOptionInline,)


@admin.register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'group', 'price_delta_syp', 'is_default', 'is_active', 'sort_order')
    list_filter = ('group', 'is_default', 'is_active')
    search_fields = ('code', 'name_ar', 'name_en', 'group__name_ar', 'group__code')
    ordering = ('group__sort_order', 'sort_order', 'name_ar')


@admin.register(ProductOptionGroupAssignment)
class ProductOptionGroupAssignmentAdmin(admin.ModelAdmin):
    list_display = ('product', 'group', 'is_active', 'sort_order')
    list_filter = ('is_active', 'group')
    search_fields = ('product__name_ar', 'product__name_en', 'group__name_ar', 'group__code')
    ordering = ('product__name_ar', 'sort_order')


admin.site.register([MenuSection, Tag, PrepStation, ProductAvailability])
