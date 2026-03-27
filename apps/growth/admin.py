"""
Growth admin configuration.
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from .models import GiftInvite, PendingGiftClaim


@admin.register(GiftInvite)
class GiftInviteAdmin(admin.ModelAdmin):
    """
    Admin for GiftInvite.

    Creation is DISABLED - use the Send Gift page instead.
    """

    list_display = [
        "id",
        "recipient_email_masked",
        "status",
        "gift_subscription_link",
        "claimed_by",
        "claimed_at",
        "expires_at",
        "is_expired_display",
        "email_sent_at",
        "created_at",
    ]

    list_filter = [
        "status",
        "created_at",
        "expires_at",
        "email_sent_at",
    ]

    search_fields = [
        "recipient_email",
        "claim_token_hash",
        "gift_subscription__gift_code",
    ]

    readonly_fields = [
        "id",
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

    # Use custom changelist template with Send Gift button
    change_list_template = "admin/growth/giftinvite/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return False

    def recipient_email_masked(self, obj):
        if obj.recipient_email:
            parts = obj.recipient_email.split("@")
            if len(parts) == 2:
                local, domain = parts
                masked_local = local[:2] + "***" if len(local) > 2 else "***"
                return f"{masked_local}@{domain}"
        return obj.recipient_email
    recipient_email_masked.short_description = _("Recipient Email")

    def gift_subscription_link(self, obj):
        if obj.gift_subscription:
            url = reverse(
                "admin:subscriptions_giftsubscription_change",
                args=[obj.gift_subscription.id]
            )
            return format_html(
                '<a href="{}">{}</a>',
                url,
                obj.gift_subscription.gift_code[:8] + "..."
            )
        return "-"
    gift_subscription_link.short_description = _("Gift Subscription")

    def is_expired_display(self, obj):
        if obj.status == GiftInvite.Status.CLAIMED:
            return "Claimed"
        if obj.is_expired:
            return "Expired"
        days_left = (obj.expires_at - timezone.now()).days
        return f"Active ({days_left} days left)"
    is_expired_display.short_description = _("Expiration Status")


@admin.register(PendingGiftClaim)
class PendingGiftClaimAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "claim_token_short",
        "status",
        "session_key_short",
        "processed_by",
        "created_at",
        "is_stale_display",
    ]

    list_filter = [
        "status",
        "created_at",
        "processed_at",
    ]

    search_fields = [
        "claim_token_hash",
        "session_key",
        "processed_by__username",
    ]

    readonly_fields = [
        "id",
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

    def has_add_permission(self, request):
        return False

    def claim_token_short(self, obj):
        return obj.claim_token_hash[:16] + "..."
    claim_token_short.short_description = _("Token Hash")

    def session_key_short(self, obj):
        return obj.session_key[:8] + "..." if obj.session_key else "-"
    session_key_short.short_description = _("Session Key")

    def is_stale_display(self, obj):
        if obj.status == PendingGiftClaim.Status.PROCESSED:
            return "Processed"
        if obj.is_stale:
            return "Stale (>7 days)"
        return "Valid"
    is_stale_display.short_description = _("Stale Status")


# Set custom admin index template to show Send Gift button
admin.site.index_template = "admin/custom_index.html"
