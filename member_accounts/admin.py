from django.contrib import admin

from member_accounts.models import MemberAccount, MemberInvitation


@admin.register(MemberAccount)
class MemberAccountAdmin(admin.ModelAdmin):
    list_display = ('member', 'status', 'claimed_at', 'last_login_at', 'created_at')
    list_filter = ('status', 'claimed_at')
    search_fields = ('member__name_ar', 'member__name_en', 'member__phone', 'email')
    autocomplete_fields = ('member',)
    readonly_fields = ('uuid', 'claimed_at', 'last_login_at', 'created_at', 'updated_at')


@admin.register(MemberInvitation)
class MemberInvitationAdmin(admin.ModelAdmin):
    list_display = ('target_member', 'invited_phone', 'purpose', 'expires_at', 'claimed_at', 'revoked_at', 'created_by')
    list_filter = ('purpose', 'claimed_at', 'revoked_at')
    search_fields = ('target_member__name_ar', 'target_member__phone', 'invited_phone', 'invited_name')
    autocomplete_fields = ('target_member', 'claimed_member', 'created_by')
    readonly_fields = ('uuid', 'token_hash', 'claimed_at', 'created_at')

    def has_add_permission(self, request):
        return False
