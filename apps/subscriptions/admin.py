"""
Admin configuration for subscriptions - Phase 3B.
"""
from django.contrib import admin

from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    PlanDiscount, GiftSubscription, SubscriptionEvent
)


class PlanPriceInline(admin.TabularInline):
    """Inline admin for plan prices."""
    model = PlanPrice
    extra = 1
    fields = ["interval", "price_cents", "currency", "is_active"]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin for subscription plans."""

    list_display = [
        "name",
        "tier",
        "upgrade_priority",
        "is_active",
        "display_order",
        "max_projects",
        "max_storage_mb",
        "created_at",
    ]
    list_filter = ["tier", "is_active"]
    search_fields = ["name", "description"]
    ordering = ["display_order", "tier"]
    inlines = [PlanPriceInline]
    fieldsets = (
        ("Plan Information", {
            "fields": ("tier", "name", "description", "is_active", "display_order", "upgrade_priority")
        }),
        ("Feature Limits", {
            "fields": ("max_projects", "max_storage_mb", "api_calls_per_day"),
            "classes": ("collapse",)
        }),
    )


@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    """Admin for plan pricing."""

    list_display = [
        "plan",
        "interval",
        "price_cents",
        "currency",
        "is_active",
    ]
    list_filter = ["interval", "currency", "is_active"]
    search_fields = ["plan__name"]
    list_select_related = ["plan"]


@admin.register(PlanDiscount)
class PlanDiscountAdmin(admin.ModelAdmin):
    """Admin for plan discounts."""

    list_display = [
        "code",
        "discount_type",
        "discount_value",
        "use_count",
        "max_uses",
        "is_active",
        "valid_until",
    ]
    list_filter = ["discount_type", "is_active"]
    search_fields = ["code", "description"]
    filter_horizontal = ["applicable_plans"]
    readonly_fields = ["use_count", "created_at", "updated_at"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin for user subscriptions."""

    list_display = [
        "user",
        "plan",
        "status",
        "is_active",
        "is_admin_grant",
        "is_trial",
        "started_at",
        "expires_at",
    ]
    list_filter = [
        "status", "is_active", "plan__tier", 
        "is_admin_grant", "is_trial"
    ]
    search_fields = ["user__username", "user__email", "provider_subscription_id"]
    list_select_related = ["user", "plan", "plan_price"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("User & Plan", {
            "fields": ("user", "plan", "plan_price", "applied_discount")
        }),
        ("Status", {
            "fields": ("status", "is_active", "is_trial", "trial_days")
        }),
        ("Admin Grant", {
            "fields": ("is_admin_grant", "granted_by", "granted_reason"),
            "classes": ("collapse",)
        }),
        ("Dates", {
            "fields": ("started_at", "expires_at", "canceled_at")
        }),
        ("Payment & Credit", {
            "fields": ("payment_provider", "provider_subscription_id", "prorated_credit_cents"),
            "classes": ("collapse",)
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    actions = ["cancel_subscriptions"]

    @admin.action(description="Cancel selected subscriptions")
    def cancel_subscriptions(self, request, queryset):
        """Bulk cancel subscriptions."""
        for sub in queryset:
            sub.cancel("Bulk admin cancellation")


@admin.register(GiftSubscription)
class GiftSubscriptionAdmin(admin.ModelAdmin):
    """Admin for gift subscriptions."""

    list_display = [
        "code",
        "plan",
        "sender",
        "status",
        "redeemed_by",
        "created_at",
        "expires_at",
    ]
    list_filter = ["status", "plan__tier"]
    search_fields = ["code", "sender__username", "recipient_email"]
    readonly_fields = ["code", "created_at", "updated_at"]
    date_hierarchy = "created_at"


@admin.register(SubscriptionHistory)
class SubscriptionHistoryAdmin(admin.ModelAdmin):
    """Admin for subscription history (read-only)."""

    list_display = [
        "subscription",
        "user",
        "event_type",
        "created_at",
    ]
    list_filter = ["event_type"]
    search_fields = ["user__username", "notes"]
    list_select_related = ["subscription", "user"]
    readonly_fields = [
        "subscription", "user", "event_type",
        "previous_plan_id", "new_plan_id",
        "previous_status", "new_status",
        "metadata", "notes", "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    """Admin for subscription events."""

    list_display = [
        "event_type",
        "user",
        "subscription",
        "processed",
        "created_at",
    ]
    list_filter = ["event_type", "processed"]
    search_fields = ["user__username"]
    readonly_fields = ["created_at"]
    date_hierarchy = "created_at"

    actions = ["mark_processed"]

    @admin.action(description="Mark selected events as processed")
    def mark_processed(self, request, queryset):
        """Bulk mark events as processed."""
        for event in queryset:
            event.mark_processed()
