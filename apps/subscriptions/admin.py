"""
Admin configuration for subscriptions with unified Plan + Geo Pricing management.
"""
from django.contrib import admin
from django.utils.html import format_html
from django import forms
from django.core.exceptions import ValidationError

from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    UpgradeHistory, GiftSubscription, GeoPlanPrice
)


# =============================================================================
# ADMIN FORM VALIDATION
# =============================================================================

class GeoPlanPriceForm(forms.ModelForm):
    """Form for GeoPlanPrice with price_cents validation."""

    class Meta:
        model = GeoPlanPrice
        fields = '__all__'

    def clean_price_cents(self):
        """Validate price_cents is a non-negative integer."""
        price_cents = self.cleaned_data.get('price_cents')

        # Check if value is None
        if price_cents is None:
            raise ValidationError("Price is required.")

        # Check if value is integer (PositiveIntegerField should handle this, but double-check)
        try:
            price_cents = int(price_cents)
        except (TypeError, ValueError):
            raise ValidationError("Price must be a whole number (no letters or decimals).")

        # Check non-negative
        if price_cents < 0:
            raise ValidationError("Price cannot be negative.")

        return price_cents


class PlanPriceForm(forms.ModelForm):
    """Form for PlanPrice with price_cents validation."""

    class Meta:
        model = PlanPrice
        fields = '__all__'

    def clean_price_cents(self):
        """Validate price_cents is a non-negative integer."""
        price_cents = self.cleaned_data.get('price_cents')

        if price_cents is None:
            raise ValidationError("Price is required.")

        try:
            price_cents = int(price_cents)
        except (TypeError, ValueError):
            raise ValidationError("Price must be a whole number (no letters or decimals).")

        if price_cents < 0:
            raise ValidationError("Price cannot be negative.")

        return price_cents


# =============================================================================
# INLINE ADMIN CLASSES
# =============================================================================

class PlanPriceInline(admin.TabularInline):
    """Inline admin for base plan prices (global pricing)."""
    model = PlanPrice
    form = PlanPriceForm
    extra = 1
    fields = ["interval", "price_cents", "currency", "is_active"]
    verbose_name = "Base Price (Global)"
    verbose_name_plural = "Base Prices (Global - Managed Here)"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Add number input type for better UX
        formset.form.base_fields['price_cents'].widget.attrs['type'] = 'number'
        formset.form.base_fields['price_cents'].widget.attrs['min'] = '0'
        formset.form.base_fields['price_cents'].widget.attrs['step'] = '1'
        return formset


class GeoPlanPriceInline(admin.TabularInline):
    """Inline admin for geo-specific plan prices - OVERRIDES ONLY."""
    model = GeoPlanPrice
    form = GeoPlanPriceForm
    extra = 0
    fields = ["interval", "price_cents", "currency", "country", "region", "is_active", "price_type_badge"]
    readonly_fields = ["price_type_badge"]
    verbose_name = "Geo Price Override"
    verbose_name_plural = "Geo Price Overrides (Country/Region Specific)"

    class Media:
        css = {
            'all': ('admin/css/widgets.css',)
        }

    def price_type_badge(self, obj=None):
        """Display compact price type badge with emoji + code."""
        if obj and obj.pk:
            if obj.country:
                return format_html(
                    '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                    'background:#e8f5e9;color:#2e7d32;border-radius:4px;font-size:12px;'
                    'font-weight:500;border:1px solid #c8e6c9;">🇺🇳 {}</span>',
                    obj.country.upper()
                )
            elif obj.region:
                return format_html(
                    '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                    'background:#fff3e0;color:#ef6c00;border-radius:4px;font-size:12px;'
                    'font-weight:500;border:1px solid #ffe0b2;">🌎 {}</span>',
                    obj.region.upper()
                )
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                'background:#ffebee;color:#c62828;border-radius:4px;font-size:12px;'
                'font-weight:500;border:1px solid #ffcdd2;">⚠️ REQUIRED</span>'
            )
        return format_html(
            '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
            'color:#9e9e9e;font-size:12px;font-style:italic;">Save to see type</span>'
        )
    price_type_badge.short_description = "Type"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Add number input type and validation attributes
        formset.form.base_fields['price_cents'].widget.attrs['type'] = 'number'
        formset.form.base_fields['price_cents'].widget.attrs['min'] = '0'
        formset.form.base_fields['price_cents'].widget.attrs['step'] = '1'
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
            '{} | {}',
            format_html(
                '<span style="color:#2e7d32;font-weight:500;">{}</span>',
                base_label
            ) if base_count else format_html('<span style="color:#9e9e9e;">No base</span>'),
            format_html(
                '<span style="color:#1565c0;font-weight:500;">{}</span>',
                geo_label
            ) if geo_count else format_html('<span style="color:#9e9e9e;">No geo</span>')
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

    form = GeoPlanPriceForm

    list_display = [
        "plan",
        "interval",
        "price_cents",
        "currency",
        "geo_badge",
        "price_type_badge",
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
**⚠️ REQUIRED: Specify either COUNTRY or REGION (not both empty)**

• **Country-specific:** Enter country code (e.g., IN, US, DE), leave region empty

• **Regional:** Enter region code (e.g., APAC, EU, NA), leave country empty

• **Global pricing is managed in PlanPrice, NOT here**

GeoPlanPrice is for **overrides only**.
"""
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
    )

    def geo_badge(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                'background:#e3f2fd;color:#1565c0;border-radius:4px;font-size:12px;'
                'font-weight:500;border:1px solid #bbdefb;">🇺🇳 {}</span>',
                obj.country.upper()
            )
        elif obj.region:
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                'background:#f3e5f5;color:#7b1fa2;border-radius:4px;font-size:12px;'
                'font-weight:500;border:1px solid #e1bee7;">🌎 {}</span>',
                obj.region.upper()
            )
        return format_html(
            '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
            'background:#ffebee;color:#c62828;border-radius:4px;font-size:12px;'
            'font-weight:500;border:1px solid #ffcdd2;">⚠️ INVALID - No geo specified</span>'
        )
    geo_badge.short_description = "Override Target"

    def price_type_badge(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                'background:#e8f5e9;color:#2e7d32;border-radius:4px;font-size:12px;'
                'font-weight:500;border:1px solid #c8e6c9;">🇺🇳 {}</span>',
                obj.country.upper()
            )
        elif obj.region:
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
                'background:#fff3e0;color:#ef6c00;border-radius:4px;font-size:12px;'
                'font-weight:500;border:1px solid #ffe0b2;">🌎 {}</span>',
                obj.region.upper()
            )
        return format_html(
            '<span style="display:inline-block;white-space:nowrap;padding:2px 6px;'
            'background:#ffebee;color:#c62828;border-radius:4px;font-size:12px;'
            'font-weight:500;border:1px solid #ffcdd2;">⚠️ INVALID</span>'
        )
    price_type_badge.short_description = "Type"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Add number input type for better UX
        form.base_fields['price_cents'].widget.attrs['type'] = 'number'
        form.base_fields['price_cents'].widget.attrs['min'] = '0'
        form.base_fields['price_cents'].widget.attrs['step'] = '1'
        return form


# =============================================================================
# EXISTING ADMIN (Unchanged)
# =============================================================================

@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    """Admin for base plan pricing (legacy global prices)."""

    form = PlanPriceForm

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

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['price_cents'].widget.attrs['type'] = 'number'
        form.base_fields['price_cents'].widget.attrs['min'] = '0'
        form.base_fields['price_cents'].widget.attrs['step'] = '1'
        return form


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
                '<span style="display:inline-block;white-space:nowrap;padding:2px 8px;'
                'background:#f3e5f5;color:#7b1fa2;border-radius:4px;font-size:12px;'
                'font-weight:500;">🎁 Gift</span>'
            )
        elif obj.is_admin_grant:
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 8px;'
                'background:#e8f5e9;color:#2e7d32;border-radius:4px;font-size:12px;'
                'font-weight:500;">👤 Admin</span>'
            )
        elif obj.payment_provider == "trial":
            return format_html(
                '<span style="display:inline-block;white-space:nowrap;padding:2px 8px;'
                'background:#e3f2fd;color:#1565c0;border-radius:4px;font-size:12px;'
                'font-weight:500;">🎯 Trial</span>'
            )
        return format_html(
            '<span style="display:inline-block;white-space:nowrap;padding:2px 8px;'
            'background:#fff3e0;color:#ef6c00;border-radius:4px;font-size:12px;'
            'font-weight:500;">💳 Paid</span>'
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
            '<span style="display:inline-block;white-space:nowrap;padding:2px 8px;'
            'background:{};color:#fff;border-radius:4px;font-size:12px;'
            'font-weight:500;">{}</span>',
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
