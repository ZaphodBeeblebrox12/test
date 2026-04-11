"""
Admin configuration for subscriptions with unified Plan + Geo Pricing management
and Trial Plan support.
"""
from django.contrib import admin
from django.utils.html import format_html
from django import forms
from django.core.exceptions import ValidationError

from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    UpgradeHistory, GiftSubscription, GeoPlanPrice, UserTrialUsage
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


class PlanForm(forms.ModelForm):
    """Form for Plan with trial validation."""

    class Meta:
        model = Plan
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        is_trial = cleaned_data.get('is_trial')
        trial_duration_days = cleaned_data.get('trial_duration_days')

        if is_trial:
            if not trial_duration_days:
                raise ValidationError({
                    'trial_duration_days': 'Trial duration is required for trial plans.'
                })
            if trial_duration_days < 1:
                raise ValidationError({
                    'trial_duration_days': 'Trial duration must be at least 1 day.'
                })

        return cleaned_data


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
                    '🇺🇳 <strong>{}</strong>',
                    obj.country.upper()
                )
            elif obj.region:
                return format_html(
                    '🌎 <strong>{}</strong>',
                    obj.region.upper()
                )
            return format_html(
                '<span style="color: red;">⚠️ REQUIRED</span>'
            )
        return format_html(
            '<em>Save to see type</em>'
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
# MAIN PLAN ADMIN (Unified Interface with Trial Support)
# =============================================================================

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin for subscription plans with unified base + geo pricing + trial support."""

    form = PlanForm

    list_display = [
        "name",
        "tier",
        "is_active",
        "trial_badge",
        "display_order",
        "max_projects",
        "max_storage_mb",
        "created_at",
        "pricing_summary",
    ]
    list_filter = ["tier", "is_active", "is_trial"]
    search_fields = ["name", "description"]
    ordering = ["display_order", "tier"]

    inlines = [PlanPriceInline, GeoPlanPriceInline]

    fieldsets = (
        ("Plan Information", {
            "fields": ("tier", "name", "description", "is_active", "display_order")
        }),
        ("Trial Configuration", {
            "fields": ("is_trial", "trial_duration_days"),
            "description": """
                <strong>Trial Plans:</strong><br>
                • Check "Is trial" to mark this as a one-time trial plan<br>
                • Set "Trial duration days" (e.g., 7 for 7-day trial)<br>
                • Users can only claim each trial plan once<br>
                • Trial plans work with geo pricing like regular plans
            """
        }),
        ("Feature Limits", {
            "fields": ("max_projects", "max_storage_mb", "api_calls_per_day"),
            "classes": ("collapse",)
        }),
    )

    def trial_badge(self, obj):
        """Display trial status badge."""
        if obj.is_trial:
            return format_html(
                '<span style="background: #17a2b8; color: white; padding: 2px 8px; '
                'border-radius: 4px; font-size: 11px;">🎯 TRIAL {}d</span>',
                obj.trial_duration_days
            )
        return format_html(
            '<span style="color: #6c757d;">—</span>'
        )
    trial_badge.short_description = "Trial"

    def pricing_summary(self, obj):
        """Show count of base and geo prices."""
        base_count = obj.prices.filter(is_active=True).count()
        geo_count = obj.geo_prices.filter(is_active=True).count()

        base_label = f"{base_count} base"
        geo_label = f"{geo_count} geo"

        return format_html(
            '{} | {}',
            format_html(
                '<span style="color: #28a745;">{}</span>',
                base_label
            ) if base_count else format_html('<span style="color: #6c757d;">No base</span>'),
            format_html(
                '<span style="color: #17a2b8;">{}</span>',
                geo_label
            ) if geo_count else format_html('<span style="color: #6c757d;">No geo</span>')
        )
    pricing_summary.short_description = "Pricing"


# =============================================================================
# USER TRIAL USAGE ADMIN
# =============================================================================

@admin.register(UserTrialUsage)
class UserTrialUsageAdmin(admin.ModelAdmin):
    """Admin for tracking trial usage (read-only audit log)."""

    list_display = [
        "user",
        "plan",
        "used_at",
        "expires_at",
        "status_badge",
    ]
    list_filter = ["plan", "used_at"]
    search_fields = ["user__username", "user__email", "plan__name"]
    list_select_related = ["user", "plan", "subscription"]
    readonly_fields = [
        "user", "plan", "subscription", "used_at", "expires_at"
    ]
    date_hierarchy = "used_at"

    def status_badge(self, obj):
        """Display trial status."""
        if obj.is_expired:
            return format_html(
                '<span style="background: #6c757d; color: white; padding: 2px 8px; '
                'border-radius: 4px; font-size: 11px;">EXPIRED</span>'
            )
        return format_html(
            '<span style="background: #28a745; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 11px;">ACTIVE</span>'
        )
    status_badge.short_description = "Status"

    def has_add_permission(self, request):
        """Prevent manual creation - trials are created via purchase flow."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent editing - this is an audit log."""
        return False


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
<strong>⚠️ REQUIRED: Specify either COUNTRY or REGION (not both empty)</strong>

• <strong>Country-specific:</strong> Enter country code (e.g., IN, US, DE), leave region empty

• <strong>Regional:</strong> Enter region code (e.g., APAC, EU, NA), leave country empty

• <strong>Global pricing is managed in PlanPrice, NOT here</strong>

GeoPlanPrice is for <strong>overrides only</strong>.
"""
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
    )

    def geo_badge(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return format_html(
                '🇺🇳 <strong>{}</strong>',
                obj.country.upper()
            )
        elif obj.region:
            return format_html(
                '🌎 <strong>{}</strong>',
                obj.region.upper()
            )
        return format_html(
            '<span style="color: red;">⚠️ INVALID - No geo specified</span>'
        )
    geo_badge.short_description = "Override Target"

    def price_type_badge(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return format_html(
                '🇺🇳 <strong>{}</strong>',
                obj.country.upper()
            )
        elif obj.region:
            return format_html(
                '🌎 <strong>{}</strong>',
                obj.region.upper()
            )
        return format_html(
            '<span style="color: red;">⚠️ INVALID</span>'
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
        "is_trial",
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
        ("Gift / Admin Grant / Trial", {
            "fields": (
                "is_gift", "gift_from", "gift_message",
                "is_admin_grant", "granted_by", "grant_reason",
                "is_trial",
            ),
            "classes": ("collapse",)
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def source_display(self, obj: Subscription) -> str:
        if obj.is_trial:
            return format_html(
                '<span style="background: #17a2b8; color: white; padding: 2px 8px; '
                'border-radius: 4px; font-size: 11px;">🎯 TRIAL</span>'
            )
        if obj.is_gift:
            return format_html(
                '<span style="background: #e83e8c; color: white; padding: 2px 8px; '
                'border-radius: 4px; font-size: 11px;">🎁 GIFT</span>'
            )
        elif obj.is_admin_grant:
            return format_html(
                '<span style="background: #6610f2; color: white; padding: 2px 8px; '
                'border-radius: 4px; font-size: 11px;">👤 ADMIN</span>'
            )
        return format_html(
            '<span style="background: #28a745; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 11px;">💳 PAID</span>'
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
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 11px;">{}</span>',
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
