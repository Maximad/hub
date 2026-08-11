from django.contrib import admin
from .models import MemberAttribute, MembershipPlan, MembershipSubscription, MembershipBenefitRule, MemberCreditLedger


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'code', 'billing_period', 'price_syp', 'is_active', 'visible_to_staff', 'visible_to_customer', 'sort_order')
    list_filter = ('billing_period', 'is_active', 'visible_to_staff', 'visible_to_customer')
    search_fields = ('name_ar', 'name_en', 'code')


@admin.register(MembershipSubscription)
class MembershipSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('member', 'plan', 'status', 'starts_at', 'ends_at', 'gross_amount_syp', 'created_by')
    list_filter = ('status', 'plan')
    search_fields = ('member__name_ar', 'member__phone', 'plan__name_ar', 'plan__code')
    autocomplete_fields = ('member', 'plan', 'order', 'payment', 'created_by')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'starts_at'


@admin.register(MemberAttribute)
class MemberAttributeAdmin(admin.ModelAdmin):
    list_display = ('member', 'code', 'label_ar', 'granted_at', 'expires_at', 'granted_by')
    list_filter = ('code', 'granted_at', 'expires_at')
    search_fields = ('member__name_ar', 'member__phone', 'code', 'label_ar', 'notes')
    autocomplete_fields = ('member', 'granted_by')
    date_hierarchy = 'granted_at'


@admin.register(MemberCreditLedger)
class MemberCreditLedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_readonly_fields(self, request, obj=None):
        return tuple(f.name for f in self.model._meta.fields)

    list_display = ('member', 'subscription', 'change_type', 'minutes_delta', 'credit_delta_syp', 'created_by', 'created_at')
    list_filter = ('change_type',)
    search_fields = ('member__name_ar', 'member__phone', 'notes')


admin.site.register(MembershipBenefitRule)
