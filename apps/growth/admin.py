"""
Admin configuration for growth app.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    GiftInvite,
    PendingGiftClaim,
    ReferralCode,
    Referral,
    ReferralSettings,
    ReferralReward,
    ReferralRewardLedger,
)


@admin.register(GiftInvite)
class GiftInviteAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'recipient_email', 'status', 'is_claimable_display',
        'claimed_by', 'created_at', 'expires_at'
    ]
    list_filter = ['status', 'created_at', 'expires_at']
    search_fields = ['recipient_email', 'claimed_by__email', 'claimed_by__username']
    readonly_fields = [
        'id', 'claim_token_hash', 'recipient_email_hash',
        'created_at', 'updated_at'
    ]
    date_hierarchy = 'created_at'

    def is_claimable_display(self, obj):
        return obj.is_claimable
    is_claimable_display.boolean = True
    is_claimable_display.short_description = 'Claimable'


@admin.register(PendingGiftClaim)
class PendingGiftClaimAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'claim_token_hash_short', 'session_key_short',
        'status', 'created_at', 'is_stale'
    ]
    list_filter = ['status', 'created_at']
    readonly_fields = ['created_at']

    def claim_token_hash_short(self, obj):
        return obj.claim_token_hash[:16] + '...'
    claim_token_hash_short.short_description = 'Token Hash'

    def session_key_short(self, obj):
        return obj.session_key[:16] + '...'
    session_key_short.short_description = 'Session'


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'user', 'created_at']
    search_fields = ['code', 'user__email', 'user__username']
    readonly_fields = ['created_at']


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'referrer', 'referred_user', 'status',
        'created_at', 'completed_at'
    ]
    list_filter = ['status', 'created_at']
    search_fields = [
        'referrer__email', 'referrer__username',
        'referred_user__email', 'referred_user__username'
    ]
    readonly_fields = ['created_at', 'completed_at']
    date_hierarchy = 'created_at'


@admin.register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'rewards_enabled', 'default_reward_percentage',
        'minimum_purchase_amount_cents', 'reward_delay_hours', 'updated_at'
    ]
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {
            'fields': ('rewards_enabled',)
        }),
        ('Reward Configuration', {
            'fields': ('default_reward_percentage', 'minimum_purchase_amount_cents', 'reward_delay_hours')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        # Only allow one settings object
        return not ReferralSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of settings
        return False


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'referrer', 'amount_display', 'status',
        'is_unlocked_display', 'unlocked_at', 'created_at'
    ]
    list_filter = ['status', 'created_at', 'currency']
    search_fields = [
        'referrer__email', 'referrer__username',
        'referral__referred_user__email'
    ]
    readonly_fields = [
        'id', 'created_at', 'updated_at', 'is_unlocked',
        'available_amount_cents'
    ]
    date_hierarchy = 'created_at'
    raw_id_fields = ['triggering_subscription']

    def is_unlocked_display(self, obj):
        if obj.status == ReferralReward.Status.PENDING:
            if obj.is_unlocked:
                return format_html('<span style="color: green;">Ready</span>')
            else:
                return format_html('<span style="color: orange;">Waiting</span>')
        return obj.status
    is_unlocked_display.short_description = 'Unlock Status'


@admin.register(ReferralRewardLedger)
class ReferralRewardLedgerAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'reward_short', 'transaction_type', 'amount_cents',
        'balance_after_cents', 'created_at'
    ]
    list_filter = ['transaction_type', 'created_at']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'

    def reward_short(self, obj):
        return f"{str(obj.reward.id)[:8]}..."
    reward_short.short_description = 'Reward'
