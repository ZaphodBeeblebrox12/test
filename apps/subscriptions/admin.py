"""
Admin configuration for subscriptions with unified Plan + Geo Pricing management.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    UpgradeHistory, GiftSubscription, GeoPlanPrice
)


# =============================================================================
# INLINE ADMIN CLASSES
# =============================================================================

class PlanPriceInline(admin.TabularInline):
    """Inline admin for base plan prices (global pricing)."""
    model = PlanPrice
    extra = 1
    fields = ["interval", "price_cents", "currency", "is_active"]
    verbose_name = "Base Price (Global)"
    verbose_name_plural = "Base Prices (Global - Managed Here)"


class GeoPlanPriceInline(admin.TabularInline):
    """Inline admin for geo-specific plan prices - OVERRIDES ONLY."""
    model = GeoPlanPrice
    extra = 0
    fields = ["interval", "price_cents", "currency", "country", "region", "is_active", "price_type_display"]
    readonly_fields = ["price_type_display"]
    verbose_name = "Geo Price Override"
    verbose_name_plural = "Geo Price Overrides (Country/Region Specific)"

    class Media:
        css = {
            'all': ('admin/css/widgets.css',)
        }

    def price_type_display(self, obj=None):
        """Display price type badge."""
        if obj and obj.pk:
            if obj.country:
                return format_html(
                    '<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em;">🇺🇳 Country: {}</span>',
                    obj.country
                )
            elif obj.region:
                return format_html(
                    '<span style="background: #17a2b8; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em;">🌎 Region: {}</span>',
                    obj.region
                )
            return format_html(
                '<span style="background: #dc3545; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em;">⚠️ REQUIRED</span>'
            )
        return format_html(
            '<span style="color: #6c757d; font-style: italic;">Save to see type</span>'
        )
    price_type_display.short_description = "Type"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['country'].widget.attrs['placeholder'] = 'e.g., IN, US, DE'
        formset.form.base_fields['country'].help_text = "Country code for country-specific override. Leave empty if using region."
        formset.form.base_fields['region'].widget.attrs['placeholder'] = 'e.g., APAC, EU, NA'
        formset.form.base_fields['region'].help_text = "Region code for regional override. Leave empty if using country."
        return formset


# =============================================================================
# MAIN PLAN ADMIN (Unified Interface)
# =============================================================================

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin for subscription plans with unified base + geo pricing."""

    list_display = [
        "name",
        "tier",
        "is_active",
        "display_order",
        "max_projects",
        "max_storage_mb",
        "created_at",
        "pricing_summary",
    ]
    list_filter = ["tier", "is_active"]
    search_fields = ["name", "description"]
    ordering = ["display_order", "tier"]

    inlines = [PlanPriceInline, GeoPlanPriceInline]

    fieldsets = (
        ("Plan Information", {
            "fields": ("tier", "name", "description", "is_active", "display_order")
        }),
        ("Feature Limits", {
            "fields": ("max_projects", "max_storage_mb", "api_calls_per_day"),
            "classes": ("collapse",)
        }),
    )

    def pricing_summary(self, obj):
        """Show count of base and geo prices."""
        base_count = obj.prices.filter(is_active=True).count()
        geo_count = obj.geo_prices.filter(is_active=True).count()

        base_label = f"{base_count} base"
        geo_label = f"{geo_count} geo"

        return format_html(
            '<span style="font-size: 0.9em;">{} <span style="color: #6c757d;">|</span> {}</span>',
            format_html(
                '<span style="background: #e9ecef; padding: 1px 6px; border-radius: 3px;">{}</span>',
                base_label
            ) if base_count else format_html('<span style="color: #dc3545;">No base</span>'),
            format_html(
                '<span style="background: #d4edda; padding: 1px 6px; border-radius: 3px;">{}</span>',
                geo_label
            ) if geo_count else format_html('<span style="color: #6c757d;">No geo</span>')
        )
    pricing_summary.short_description = "Pricing"


# =============================================================================
# STANDALONE GEO PLAN PRICE ADMIN (Optional/Advanced Use)
# =============================================================================

@admin.register(GeoPlanPrice)
class GeoPlanPriceAdmin(admin.ModelAdmin):
    """
    Standalone admin for GeoPlanPrice - OVERRIDES ONLY.

    Note: Normal workflow should use the inline on PlanAdmin.
    This is for advanced/bulk management only.
    """

    list_display = [
        "plan",
        "interval",
        "price_cents",
        "currency",
        "geo_display",
        "price_type_display",
        "is_active",
    ]
    list_filter = [
        "interval",
        "currency",
        "is_active",
        "region",
        "country",
        "plan",
    ]
    search_fields = ["plan__name", "country", "region"]
    list_select_related = ["plan"]

    fieldsets = (
        ("Plan & Interval", {
            "fields": ("plan", "interval")
        }),
        ("Pricing", {
            "fields": ("price_cents", "currency")
        }),
        ("Geo Override Target", {
            "fields": ("country", "region"),
            "description": """
                <b>⚠️ REQUIRED: Specify either COUNTRY or REGION (not both empty)</b><br><br>
                • <b>Country-specific:</b> Enter country code (e.g., IN, US, DE), leave region empty<br>
                • <b>Regional:</b> Enter region code (e.g., APAC, EU, NA), leave country empty<br>
                • <b>Global pricing is managed in PlanPrice, NOT here</b><br><br>
                GeoPlanPrice is for <b>overrides only</b>.
            """
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
    )

    def geo_display(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return format_html(
                '<span style="color: green; font-weight: bold;">🇺🇳 {}</span>',
                obj.country
            )
        elif obj.region:
            return format_html(
                '<span style="color: blue;">🌎 {}</span>',
                obj.region
            )
        return format_html(
            '<span style="color: #dc3545; font-weight: bold;">⚠️ INVALID - No geo specified</span>'
        )
    geo_display.short_description = "Override Target"

    def price_type_display(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return format_html(
                '<span style="background: #d4edda; padding: 2px 6px; border-radius: 3px;">Country</span>'
            )
        elif obj.region:
            return format_html(
                '<span style="background: #d1ecf1; padding: 2px 6px; border-radius: 3px;">Region</span>'
            )
        return format_html(
            '<span style="background: #dc3545; color: white; padding: 2px 6px; border-radius: 3px;">INVALID</span>'
        )
    price_type_display.short_description = "Type"


# =============================================================================
# EXISTING ADMIN (Unchanged)
# =============================================================================

@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    """Admin for base plan pricing (legacy global prices)."""

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
        "source_display",
    ]
    list_filter = [
        "status",
        "is_active",
        "plan__tier",
        "is_gift",
        "is_admin_grant",
    ]
    search_fields = [
        "user__username",
        "user__email",
        "provider_subscription_id",
    ]
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
        ("Geo Pricing", {
            "fields": ("pricing_country", "pricing_region"),
            "classes": ("collapse",)
        }),
        ("Gift / Admin Grant", {
            "fields": (
                "is_gift", "gift_from", "gift_message",
                "is_admin_grant", "granted_by", "grant_reason"
            ),
            "classes": ("collapse",)
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def source_display(self, obj: Subscription) -> str:
        if obj.is_gift:
            return format_html(
                '<span style="background: #fff3cd; padding: 2px 6px; border-radius: 3px;">🎁 Gift</span>'
            )
        elif obj.is_admin_grant:
            return format_html(
                '<span style="background: #d4edda; padding: 2px 6px; border-radius: 3px;">👤 Admin</span>'
            )
        elif obj.payment_provider == "trial":
            return format_html(
                '<span style="background: #d1ecf1; padding: 2px 6px; border-radius: 3px;">🎯 Trial</span>'
            )
        return format_html(
            '<span style="background: #f8f9fa; padding: 2px 6px; border-radius: 3px;">💳 Paid</span>'
        )
    source_display.short_description = "Source"


@admin.register(SubscriptionHistory)
class SubscriptionHistoryAdmin(admin.ModelAdmin):
    """Admin for subscription history (read-only)."""

    list_display = [
        "subscription",
        "user",
        "event_type",
        "event_badge",
        "created_at",
    ]
    list_filter = ["event_type", "created_at"]
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

    def event_badge(self, obj: SubscriptionHistory) -> str:
        colors = {
            "created": "#6c757d",
            "activated": "#28a745",
            "renewed": "#17a2b8",
            "canceled": "#dc3545",
            "expired": "#6c757d",
            "upgraded": "#ffc107",
            "downgraded": "#fd7e14",
            "trial_started": "#20c997",
            "trial_expired": "#6c757d",
            "admin_granted": "#6610f2",
            "gift_received": "#e83e8c",
        }
        color = colors.get(obj.event_type, "#6c757d")
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.85em;">{}</span>',
            color,
            obj.get_event_type_display()
        )
    event_badge.short_description = "Event"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UpgradeHistory)
class UpgradeHistoryAdmin(admin.ModelAdmin):
    """Admin for upgrade history."""

    list_display = [
        "user",
        "from_plan",
        "to_plan",
        "amount_due_dollars",
        "is_successful",
        "created_at",
    ]
    list_filter = [
        "is_successful",
        "pricing_country",
        "pricing_region",
        "created_at",
    ]
    search_fields = ["user__username", "from_plan__name", "to_plan__name"]
    list_select_related = ["user", "from_plan", "to_plan"]
    readonly_fields = ["created_at"]
    date_hierarchy = "created_at"

    def amount_due_dollars(self, obj: UpgradeHistory) -> str:
        return f"${obj.amount_due_cents / 100:.2f}"
    amount_due_dollars.short_description = "Amount Due"


@admin.register(GiftSubscription)
class GiftSubscriptionAdmin(admin.ModelAdmin):
    """Admin for gift subscriptions."""

    list_display = [
        "gift_code",
        "plan",
        "from_user",
        "to_user",
        "status",
        "created_at",
    ]
    list_filter = [
        "status",
        "pricing_country",
        "pricing_region",
        "created_at",
    ]
    search_fields = [
        "gift_code",
        "from_user__username",
        "to_user__username",
        "plan__name",
    ]
    list_select_related = ["from_user", "to_user", "plan"]
    readonly_fields = ["gift_code", "created_at", "updated_at"]
    date_hierarchy = "created_at"
