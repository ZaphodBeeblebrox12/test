"""
Admin configuration for subscriptions.
"""
from django.contrib import admin

from .models import Plan, PlanPrice, Subscription, SubscriptionHistory


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
            "fields": ("tier", "name", "description", "is_active", "display_order")
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


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin for user subscriptions."""

    list_display = [
        "user",
        "plan",
        "status",
        "is_active",
        "started_at",
        "expires_at",
    ]
    list_filter = ["status", "is_active", "plan__tier"]
    search_fields = ["user__username", "user__email", "provider_subscription_id"]
    list_select_related = ["user", "plan", "plan_price"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("User & Plan", {
            "fields": ("user", "plan", "plan_price")
        }),
        ("Status", {
            "fields": ("status", "is_active")
        }),
        ("Dates", {
            "fields": ("started_at", "expires_at", "canceled_at")
        }),
        ("Payment Provider", {
            "fields": ("payment_provider", "provider_subscription_id"),
            "classes": ("collapse",)
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )


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
        "subscription",
        "user",
        "event_type",
        "previous_plan_id",
        "new_plan_id",
        "previous_status",
        "new_status",
        "metadata",
        "notes",
        "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
