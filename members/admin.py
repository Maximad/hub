from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from core.models import ActivityLog
from .models import (CommercialAllocation, MemberAttribute, MembershipPlan, MembershipSubscription,
                     MembershipBenefitRule, MemberCreditLedger, Program,
                     ProgramEnrollment)


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'code', 'billing_period', 'price_syp', 'is_active', 'visible_to_staff', 'visible_to_customer', 'sort_order')
    list_filter = ('billing_period', 'is_active', 'visible_to_staff', 'visible_to_customer')
    search_fields = ('name_ar', 'name_en', 'code')


@admin.register(MembershipSubscription)
class MembershipSubscriptionAdmin(admin.ModelAdmin):
    class ManualGrantForm(forms.ModelForm):
        class Meta:
            model = MembershipSubscription
            fields = '__all__'

        def clean(self):
            cleaned = super().clean()
            if not self.instance.pk and not (cleaned.get('notes') or '').strip():
                raise ValidationError('سبب المنحة اليدوية مطلوب. الشراء العادي يتم من شاشة بيع العضوية.')
            return cleaned

    form = ManualGrantForm
    list_display = ('member', 'plan', 'status', 'starts_at', 'ends_at', 'gross_amount_syp', 'created_by')
    list_filter = ('status', 'plan')
    search_fields = ('member__name_ar', 'member__phone', 'plan__name_ar', 'plan__code')
    autocomplete_fields = ('member', 'plan', 'order', 'payment', 'created_by')
    readonly_fields = ('sale_idempotency_key', 'sale_request_fingerprint', 'is_complimentary',
                       'activation_error', 'created_at', 'updated_at')
    date_hierarchy = 'starts_at'

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def save_model(self, request, obj, form, change):
        is_manual_grant = not change
        if is_manual_grant:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if is_manual_grant:
            ActivityLog.objects.create(
                actor=request.user, action='membership.manual_grant_created',
                details={'subscription_id': str(obj.uuid), 'member_id': obj.member_id,
                         'plan_id': obj.plan_id, 'reason': obj.notes[:500]})


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


@admin.register(MembershipBenefitRule)
class MembershipBenefitRuleAdmin(admin.ModelAdmin):
    list_display = ('plan', 'benefit_type', 'scope_type', 'scope_code', 'priority', 'is_active', 'updated_at')
    list_filter = ('benefit_type', 'scope_type', 'is_active', 'plan')
    search_fields = ('plan__name_ar', 'plan__name_en', 'plan__code', 'scope_code', 'value_text', 'notes')
    autocomplete_fields = ('plan', 'product')
    readonly_fields = ('uuid', 'created_at', 'updated_at')


@admin.register(CommercialAllocation)
class CommercialAllocationAdmin(admin.ModelAdmin):
    list_display = ('subscription', 'component_type', 'allocated_amount_syp', 'partner', 'partner_share_amount_syp', 'created_at')
    readonly_fields = tuple(field.name for field in CommercialAllocation._meta.fields)
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ('name_ar', 'code', 'status', 'starts_at', 'ends_at', 'capacity', 'is_active')
    list_filter = ('status', 'is_active', 'starts_at')
    search_fields = ('code', 'name_ar', 'name_en', 'description_ar', 'description_en')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'starts_at'


@admin.register(ProgramEnrollment)
class ProgramEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('program', 'participant', 'status', 'enrolled_at', 'effective_status')
    list_filter = ('status', 'program', 'enrolled_at')
    search_fields = ('program__code', 'program__name_ar', 'member__name_ar', 'member__phone', 'participant_name', 'notes')
    autocomplete_fields = ('program', 'member', 'subscription', 'order', 'payment')
    readonly_fields = ('created_at', 'updated_at', 'completed_at', 'cancelled_at')
    date_hierarchy = 'enrolled_at'

    @admin.display(description='المشارك')
    def participant(self, obj):
        return obj.member or obj.participant_name

    @admin.display(description='الحالة الفعلية')
    def effective_status(self, obj):
        if not obj.program.is_active and obj.status in {obj.Status.PENDING, obj.Status.ACTIVE}:
            return 'البرنامج غير نشط'
        return obj.get_status_display()
