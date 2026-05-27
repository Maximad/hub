from django.contrib import admin
from .models import MembershipPlan, MembershipSubscription, MembershipBenefitRule, MemberCreditLedger


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'code', 'billing_period', 'price_syp', 'is_active')
    list_filter = ('billing_period', 'is_active')
    search_fields = ('name_ar', 'name_en', 'code')


@admin.register(MembershipSubscription)
class MembershipSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('member', 'plan', 'status', 'starts_at', 'ends_at', 'remaining_internet_minutes', 'remaining_credit_syp')
    list_filter = ('status', 'plan')
    search_fields = ('member__name_ar', 'member__phone', 'plan__name_ar', 'plan__code')


@admin.register(MemberCreditLedger)
class MemberCreditLedgerAdmin(admin.ModelAdmin):
    list_display = ('member', 'subscription', 'change_type', 'minutes_delta', 'credit_delta_syp', 'created_by', 'created_at')
    list_filter = ('change_type',)
    search_fields = ('member__name_ar', 'member__phone', 'notes')


admin.site.register(MembershipBenefitRule)
