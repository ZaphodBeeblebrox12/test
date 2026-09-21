"""
Admin configuration for subscriptions with unified Plan + Geo Pricing management
and Trial Plan support.
"""
from django.contrib import admin, messages
from django.urls import reverse

from apps.jobs.enqueue import enqueue_reconcile
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django import forms
from django.core.exceptions import ValidationError

from apps.bot_integration.models import UserChannelAssignment
from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    UpgradeHistory, GiftSubscription, GeoPlanPrice, UserTrialUsage
)
from .services import (
    cancel_subscription,
    expire_subscription,
    extend_subscription,
)


# =============================================================================
# ADMIN FORM VALIDATION
# =============================================================================

class GeoPlanPriceForm(forms.ModelForm):
    """Form for GeoPlanPrice with price_cents validation."""

    class Meta:
        model = GeoPlanPrice
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        country = cleaned.get("country")
        region = cleaned.get("region")
        if not country and not region:
            raise ValidationError(
                "Choose a COUNTRY (e.g. IN, US, DE) for a country-specific override "
                "OR a REGION (e.g. EU, APAC, NA) for a regional override — not neither. "
                "Resolution order is: country → region → global base price."
            )
        return cleaned


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict the interval dropdown to billing intervals that don't already
        # have an active base price for this plan — prevents duplicate selection
        # that the backend would reject on Save.
        plan = self.instance.plan if self.instance.plan_id else None
        if plan is not None and "interval" in self.fields:
            taken = set(
                plan.prices.filter(is_active=True)
                .exclude(pk=self.instance.pk)
                .values_list("interval", flat=True)
            )
            self.fields["interval"].choices = [
                (val, lab) for val, lab in self.fields["interval"].choices
                if val not in taken
            ]

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

    def get_extra(self, request, obj=None, **kwargs):
        # Don't offer "Add another Base Price (Global)" once every billing
        # interval already has a base price — the backend rejects a duplicate
        # active base, so offering the row only sets the admin up to fail.
        if obj is not None:
            existing = set(obj.prices.values_list("interval", flat=True))
            all_intervals = {c[0] for c in PlanPrice._meta.get_field("interval").choices}
            if existing >= all_intervals:
                return 0
        return super().get_extra(request, obj, **kwargs)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        # Add number input type for better UX
        formset.form.base_fields['price_cents'].widget.attrs['type'] = 'number'
        formset.form.base_fields['price_cents'].widget.attrs['min'] = '0'
        formset.form.base_fields['price_cents'].widget.attrs['step'] = '1'

        # Formset-level clean: catch same-interval ACTIVE duplicates across the
        # SUBMITTED rows. For a NEW plan the rows aren't in the DB when each form
        # validates, so form.clean() can't see its siblings — this is what turns
        # the "Monthly USD + Monthly INR" case into a normal inline field error
        # (HTTP 200) instead of an uncaught ValidationError (HTTP 500).
        base_clean = formset.clean
        def _clean(self_):
            base_clean(self_)
            from apps.subscriptions.models import PlanPrice as _PP
            if getattr(formset, 'model', None) is not _PP:
                return
            seen = {}
            for form in self_.forms:
                if not hasattr(form, "cleaned_data"):
                    continue
                if form.cleaned_data.get("DELETE"):
                    continue
                if not form.cleaned_data.get("is_active"):
                    continue
                interval = form.cleaned_data.get("interval")
                if interval:
                    if interval in seen:
                        form.add_error("interval",
                            "An active base price already exists for this billing "
                            "interval. Only ONE active global base price per interval "
                            "— for a different currency/market price, add a Geo Price "
                            "Override below, not a second global base price.")
                    else:
                        seen[interval] = True
        formset.clean = _clean
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

class SubscriptionHistoryInline(admin.TabularInline):
    """Read-only chronological history on the Subscription detail page.

    SUMMARY + entry point (not a database dump): capped at the most recent 15
    events, newest first.  Fully read-only; no add/change/delete."""

    model = SubscriptionHistory
    extra = 0
    can_delete = False
    fields = ["event_badge", "previous_status", "new_status", "notes", "created_at"]
    readonly_fields = fields

    def get_queryset(self, request):
        # SUMMARY: cap at the most recent 15 events, newest first, so the
        # page stays usable for long-lived subscriptions.
        return super().get_queryset(request).order_by("-created_at")[:15]

    def has_add_permission(self, request, obj=None):
        return False

    def event_badge(self, obj):
        color = {
            SubscriptionHistory.EventType.CREATED: "#2e7d32",
            SubscriptionHistory.EventType.CANCELED: "#b71c1c",
            SubscriptionHistory.EventType.EXPIRED: "#e65100",
            SubscriptionHistory.EventType.RENEWED: "#1565c0",
        }.get(obj.event_type, "#555")
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>', color, obj.event_type)
    event_badge.short_description = "Event"


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
    autocomplete_fields = ["user", "plan"]
    readonly_fields = ["created_at", "updated_at"]

    # P3c: investigation inlines (history + telegram access), both read-only.
    inlines = [SubscriptionHistoryInline]

    # P3c: clear investigation layout.  All snapshot/lifecycle fields are
    # read-only (P3a); this only groups them for the operator.  "Snapshot"
    # vs "current plan configuration" is kept distinct.
    fieldsets = (
        ("Identity", {
            "fields": (("user_link", "plan_link", "id"),),
        }),
        ("Lifecycle", {
            "fields": (("status_badge", "is_active"),
                       ("started_at", "expires_at", "canceled_at")),
        }),
        ("Pricing snapshot (as purchased — does not change with plan config)", {
            "fields": (("price_cents", "price_currency"),
                       ("plan_price", "geo_plan_price"),
                       ("pricing_country", "pricing_region"),
                       ("payment_provider", "provider_subscription_id")),
        }),
        ("Source", {
            "fields": (("is_trial", "is_gift", "is_admin_grant"),
                       ("gift_link",), ("granted_by", "gift_from")),
        }),
        ("Investigate", {
            "fields": (("payments_link", "access_link"),
                       ("access_state",), ("jobs_link",),),
        }),
        ("Audit", {
            "fields": (("created_at", "updated_at"),),
        }),
    )

    # --- P3b: safe lifecycle actions (orchestrate services; no duplication). -
    actions = [
        "cancel_subscriptions",
        "expire_subscriptions",
        "extend_subscriptions",
        "reconcile_subscriptions",
    ]

    def _op_counts(self, results):
        ok = sum(1 for r in results if r is True)
        skipped = sum(1 for r in results if r is False)
        failed = sum(1 for r in results if r == "error")
        return ok, skipped, failed

    @admin.action(description="Cancel selected subscriptions")
    def cancel_subscriptions(self, request, queryset):
        """Orchestrates services.cancel_subscription. Idempotent; skips
        non-active rows.  Never mutates state directly."""
        results = []
        for sub in queryset.select_related("user"):
            try:
                results.append(cancel_subscription(sub, actor=request.user.username))
            except Exception:
                results.append("error")
        ok, skipped, failed = self._op_counts(results)
        self.message_user(
            request,
            f"{ok} subscription(s) canceled. "
            + (f"{skipped} skipped (not active). " if skipped else "")
            + (f"{failed} failed." if failed else ""),
            messages.SUCCESS if not failed else messages.WARNING,
        )

    @admin.action(description="Expire selected subscriptions")
    def expire_subscriptions(self, request, queryset):
        """Orchestrates services.expire_subscription (atomic claim). Only
        ACTIVE rows whose expires_at has passed are expired."""
        results = []
        for sub in queryset:
            try:
                results.append(expire_subscription(sub))
            except Exception:
                results.append("error")
        ok, skipped, failed = self._op_counts(results)
        self.message_user(
            request,
            f"{ok} subscription(s) expired. "
            + (f"{skipped} skipped (not active or not yet due). " if skipped else "")
            + (f"{failed} failed." if failed else ""),
            messages.SUCCESS if not failed else messages.WARNING,
        )

    @admin.action(description="Extend selected subscriptions (+30 days)")
    def extend_subscriptions(self, request, queryset):
        """Orchestrates services.extend_subscription (+30 days). Only ACTIVE
        rows; snapshot/plan/payment are unchanged. Idempotent."""
        results = []
        for sub in queryset:
            try:
                results.append(extend_subscription(
                    sub, days=30, actor=request.user.username))
            except Exception:
                results.append("error")
        ok, skipped, failed = self._op_counts(results)
        self.message_user(
            request,
            f"{ok} subscription(s) extended by 30 days. "
            + (f"{skipped} skipped (not active). " if skipped else "")
            + (f"{failed} failed." if failed else ""),
            messages.SUCCESS if not failed else messages.WARNING,
        )

    @admin.action(description="Reconcile Telegram access for selected")
    def reconcile_subscriptions(self, request, queryset):
        """Enqueues durable reconcile jobs (per user). Never calls Telegram
        synchronously. enqueue_reconcile dedupes by reconcile:{user_id}."""
        user_ids = set(queryset.values_list("user_id", flat=True))
        for uid in user_ids:
            enqueue_reconcile(uid, reason="admin_reconcile")
        self.message_user(
            request,
            f"Reconciliation queued for {len(user_ids)} user(s).",
            messages.SUCCESS,
        )

    # --- P3a: lifecycle & financial fields are READ-ONLY. ------------------
    # Entitlement/status/payment-snapshot are owned by the P1/P2 services
    # (verify->claim activation, expiry, cancellation), which also write
    # SubscriptionHistory and enqueue Telegram reconciliation.  Ordinary
    # Admin editing must NOT bypass that contract.  Lifecycle changes happen
    # only through controlled admin actions (future P3b), never raw edits.
    # admin-grant is preserved via its dedicated flow, not by editing these.
    SUBSCRIPTION_READONLY = [
        "user", "plan", "plan_price", "geo_plan_price", "status", "is_active",
        "started_at", "canceled_at", "expires_at", "payment_provider",
        "provider_subscription_id", "price_cents", "price_currency",
        "pricing_country", "pricing_region", "is_gift", "gift_from",
        "is_admin_grant", "granted_by",
    ]

    def access_state(self, obj):
        """Read-only Telegram access summary for this subscription's user.

        UserChannelAssignment has a USER FK (not Subscription), so this is
        computed for the single parent object -- not a per-row inline.  One
        query for the detail page; never edits entitlement state."""
        if not obj or not obj.user_id:
            return "\u2014"
        rows = list(UserChannelAssignment.objects.filter(user_id=obj.user_id)[:10])
        if not rows:
            return "No channel assignments for this user."
        parts = []
        for r in rows:
            state = "active" if r.is_active else f"revoked {r.revoked_at or ''}".strip()
            parts.append(f"{r.platform} {r.external_id} \u2014 {state}")
        return format_html("<br>".join(parts))
    access_state.short_description = "Telegram access (user)"

    def status_badge(self, obj):
        color = {
            Subscription.Status.ACTIVE: "#2e7d32",
            Subscription.Status.CANCELED: "#b71c1c",
            Subscription.Status.EXPIRED: "#e65100",
            Subscription.Status.PENDING: "#6a1b9a",
        }.get(obj.status, "#555")
        return format_html(
            '<span style="color:{};font-weight:700">{}</span>', color, obj.status)
    status_badge.short_description = "Status"

    # --- P3c: investigation navigation (no fabricated relationships). ------
    # PaymentIntent has NO Subscription FK, and GiftSubscription has NO
    # Subscription FK, so these are clearly-labeled navigation links to the
    # *filtered changelists* -- not a claim of a direct "the payment" link.

    def user_link(self, obj):
        if not obj.user_id:
            return "—"
        url = reverse("admin:accounts_user_change", args=[obj.user_id])
        return format_html('<a href="{}">{}</a>', url, obj.user)
    user_link.short_description = "User"

    def plan_link(self, obj):
        if not obj.plan_id:
            return "—"
        url = reverse("admin:subscriptions_plan_change", args=[obj.plan_id])
        return format_html('<a href="{}">{}</a>', url, obj.plan)
    plan_link.short_description = "Plan"

    def payments_link(self, obj):
        if not obj.user_id:
            return "—"
        base = reverse("admin:payments_paymentintent_changelist")
        return format_html(
            '<a href="{}?user__id__exact={}">View payment intents for this user</a>',
            base, obj.user_id)
    payments_link.short_description = "Payment intents"

    def access_link(self, obj):
        if not obj.user_id:
            return "—"
        base = reverse("admin:bot_integration_userchannelassignment_changelist")
        return format_html(
            '<a href="{}?user__id__exact={}">View Telegram assignments</a>',
            base, obj.user_id)
    access_link.short_description = "Telegram access"

    def jobs_link(self, obj):
        if not obj.user_id:
            return "—"
        base = reverse("admin:jobs_job_changelist")
        return format_html(
            '<a href="{}?payload__icontains={}">View jobs for this user</a>',
            base, str(obj.user_id))
    jobs_link.short_description = "Jobs"

    def gift_link(self, obj):
        if not obj.is_gift:
            return "—"
        base = reverse("admin:subscriptions_giftsubscription_changelist")
        return format_html('<a href="{}">View gifts for this plan</a>', base)
    gift_link.short_description = "Gift"

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        for f in self.SUBSCRIPTION_READONLY:
            if f not in base:
                base.append(f)
        return base

    def has_delete_permission(self, request, obj=None):
        # Subscriptions are financial/entitlement history; never delete.
        return False
    date_hierarchy = "created_at"


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
    # Financial/audit record: fully read-only, never add/delete.
    readonly_fields = [f.name for f in UpgradeHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
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
