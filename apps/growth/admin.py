"""
Growth app admin configuration.
Safety enforced: GiftInvite cannot be created directly in admin.
"""
from django.contrib import admin
from django.conf import settings
from django.contrib import messages

from .models import GiftInvite, PendingGiftClaim, ReferralCode, Referral


# ============================================================================
# REFERRAL ADMIN
# ============================================================================

@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "user", "created_at"]
    list_select_related = ["user"]
    search_fields = ["code", "user__username", "user__email"]
    readonly_fields = ["code", "created_at"]
    ordering = ["-created_at"]


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ["referrer", "referred_user", "status", "created_at", "completed_at"]
    list_select_related = ["referrer", "referred_user"]
    list_filter = ["status", "created_at"]
    search_fields = [
        "referrer__username",
        "referrer__email",
        "referred_user__username",
        "referred_user__email",
    ]
    readonly_fields = ["created_at", "completed_at"]
    ordering = ["-created_at"]

    actions = ["simulate_purchase"]

    @admin.action(description="Simulate purchase (DEBUG only)")
    def simulate_purchase(self, request, queryset):
        """
        Admin action to simulate a purchase for selected referrals.
        Only works in DEBUG mode.
        """
        if not settings.DEBUG:
            self.message_user(
                request,
                "Purchase simulation is only available in DEBUG mode.",
                level=messages.ERROR
            )
            return

        from .services import simulate_purchase, PurchaseSimulationError

        success_count = 0
        error_count = 0

        for referral in queryset.select_related("referred_user"):
            user = referral.referred_user

            try:
                result = simulate_purchase(user)

                if result.get("referral_completed"):
                    success_count += 1
                    self.message_user(
                        request,
                        f"✓ {user.username}: Purchase simulated, referral completed.",
                        level=messages.SUCCESS
                    )
                else:
                    self.message_user(
                        request,
                        f"⚠ {user.username}: Purchase simulated, but no pending referral found.",
                        level=messages.WARNING
                    )

            except PurchaseSimulationError as e:
                error_count += 1
                self.message_user(
                    request,
                    f"✗ {user.username}: {str(e)}",
                    level=messages.ERROR
                )

        if success_count > 0 or error_count > 0:
            summary = f"Processed {success_count + error_count} users."
            if success_count > 0:
                summary += f" {success_count} referrals completed."
            if error_count > 0:
                summary += f" {error_count} errors."

            self.message_user(request, summary, level=messages.INFO)


# ============================================================================
# GIFT ADMIN (Safety Enforced)
# ============================================================================

class ReadOnlyAdminMixin:
    """Mixin that makes admin read-only and disables add permission."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Allow deletion for cleanup
        return True


@admin.register(GiftInvite)
class GiftInviteAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    GiftInvite admin - READ ONLY.

    SAFETY: Direct creation is DISABLED.
    GiftInvite must ONLY be created through GiftService.
    """
    list_display = [
        "recipient_email",
        "status",
        "claimed_by",
        "expires_at",
        "created_at",
    ]
    list_filter = ["status", "created_at", "expires_at"]
    search_fields = ["recipient_email", "claimed_by__username", "claimed_by__email"]
    readonly_fields = [
        "gift_subscription",
        "recipient_email",
        "recipient_email_hash",
        "claim_token_hash",
        "status",
        "claimed_by",
        "claimed_at",
        "email_sent_at",
        "email_resend_count",
        "last_email_sent_at",
        "expires_at",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    fieldsets = (
        ("Gift Information", {
            "fields": ("gift_subscription", "recipient_email", "status")
        }),
        ("Claim Information", {
            "fields": ("claimed_by", "claimed_at")
        }),
        ("Email Tracking", {
            "fields": ("email_sent_at", "email_resend_count", "last_email_sent_at")
        }),
        ("Metadata", {
            "fields": ("expires_at", "created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )


@admin.register(PendingGiftClaim)
class PendingGiftClaimAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    PendingGiftClaim admin - READ ONLY.
    These are system-generated records.
    """
    list_display = ["session_key", "status", "processed_by", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["session_key", "processed_by__username"]
    readonly_fields = [
        "claim_token_hash",
        "session_key",
        "status",
        "processed_at",
        "processed_by",
        "error_message",
        "ip_address",
        "user_agent",
        "created_at",
    ]
    ordering = ["-created_at"]
