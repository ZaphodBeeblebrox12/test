"""
Growth app admin configuration.
"""
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum

from .models import (
    GiftInvite, 
    PendingGiftClaim, 
    ReferralCode, 
    Referral,
    ReferralSettings,
    ReferralReward,
    ReferralRewardLedger,
)


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "user", "created_at"]
    list_select_related = ["user"]
    search_fields = ["code", "user__username", "user__email"]
    readonly_fields = ["code", "created_at"]
    ordering = ["-created_at"]


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ["referrer", "referred_user", "status", "created_at", "completed_at", "has_reward"]
    list_select_related = ["referrer", "referred_user"]
    list_filter = ["status", "created_at"]
    search_fields = [
        "referrer__username", "referrer__email",
        "referred_user__username", "referred_user__email",
    ]
    readonly_fields = ["created_at", "completed_at"]
    ordering = ["-created_at"]

    def has_reward(self, obj):
        return hasattr(obj, 'reward') and obj.reward is not None
    has_reward.boolean = True
    has_reward.short_description = "Has Reward"


@admin.register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    list_display = ["default_reward_percentage", "minimum_purchase_amount", "rewards_enabled", "updated_at"]
    readonly_fields = ["created_at", "updated_at"]

    def minimum_purchase_amount(self, obj):
        return f"${obj.minimum_purchase_amount_cents / 100:.2f}"
    minimum_purchase_amount.short_description = "Min Purchase Amount"

    def has_add_permission(self, request):
        return not ReferralSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class ReferralRewardLedgerInline(admin.TabularInline):
    model = ReferralRewardLedger
    extra = 0
    readonly_fields = ["transaction_type", "amount_cents", "balance_after_cents", "description", "created_at"]
    can_delete = False
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ["referrer", "amount_display", "available_amount", "status", "referred_user", "created_at"]
    list_select_related = ["referrer", "referral__referred_user"]
    list_filter = ["status", "created_at", "currency"]
    search_fields = [
        "referrer__username", "referrer__email",
        "referral__referred_user__username",
    ]
    readonly_fields = [
        "id", "referral", "referrer", "amount_cents", "currency",
        "referred_purchase_amount_cents", "reward_percentage",
        "used_amount_cents", "used_at", "created_at", "updated_at",
    ]
    ordering = ["-created_at"]
    inlines = [ReferralRewardLedgerInline]

    def amount_display(self, obj):
        return f"${obj.amount_cents / 100:.2f} {obj.currency}"
    amount_display.short_description = "Reward Amount"

    def available_amount(self, obj):
        available = obj.available_amount_cents
        if available < obj.amount_cents:
            return format_html(
                '<span style="color: #999;">${:.2f}</span> / ${:.2f}',
                available / 100, obj.amount_cents / 100
            )
        return f"${available / 100:.2f}"
    available_amount.short_description = "Available"

    def referred_user(self, obj):
        if obj.referral and obj.referral.referred_user:
            return obj.referral.referred_user.username
        return "-"
    referred_user.short_description = "Referred User"

    def has_add_permission(self, request):
        return False


@admin.register(ReferralRewardLedger)
class ReferralRewardLedgerAdmin(admin.ModelAdmin):
    list_display = ["reward", "transaction_type", "amount_cents", "balance_after_cents", "created_at"]
    list_filter = ["transaction_type", "created_at"]
    search_fields = ["reward__referrer__username", "description"]
    readonly_fields = [
        "reward", "transaction_type", "amount_cents", "balance_after_cents",
        "description", "subscription", "metadata", "created_at",
    ]
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return True


@admin.register(GiftInvite)
class GiftInviteAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["recipient_email", "status", "claimed_by", "expires_at", "created_at"]
    list_filter = ["status", "created_at", "expires_at"]
    search_fields = ["recipient_email", "claimed_by__username", "claimed_by__email"]
    readonly_fields = [
        "gift_subscription", "recipient_email", "recipient_email_hash", "claim_token_hash",
        "status", "claimed_by", "claimed_at", "email_sent_at", "email_resend_count",
        "last_email_sent_at", "expires_at", "created_at", "updated_at",
    ]
    ordering = ["-created_at"]


@admin.register(PendingGiftClaim)
class PendingGiftClaimAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["session_key", "status", "processed_by", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["session_key", "processed_by__username"]
    readonly_fields = [
        "claim_token_hash", "session_key", "status", "processed_at", "processed_by",
        "error_message", "ip_address", "user_agent", "created_at",
    ]
    ordering = ["-created_at"]
