"""Campaign admin (G2) -- single campaign workspace.

The campaign change page is the operator hub: audience (with live size),
email content (the send sweep's single source of truth), optional extra
channel actions, schedule/state, recipients, and results -- without hunting
through separate admin sections.  Reusable libraries (Audience, Templates,
Announcements) keep their own changelists; per-run data (recipients,
metrics) lives here.
"""

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.advanced_growth.models import CampaignAction
from apps.jobs.enqueue import enqueue_generic

from .models import Audience, Campaign, CampaignMetrics, CampaignRecipient


class CampaignActionInline(admin.TabularInline):
    """Delivery actions for channels BEYOND the campaign's built-in email.

    Email content is configured on the campaign itself (template_version +
    subject_override) and is executed by the campaign send sweep -- the
    email channel is deliberately excluded from this inline so there is
    never a second email source of truth.

    Telegram marketing and in-product announcements are configured here.
    NOTE: delivery automation for these channels is NOT wired into the
    campaign send sweep yet -- rows are staged as "queued" and are not
    executed by any job.
    """
    model = CampaignAction
    extra = 0
    fields = ["channel", "template_version", "announcement", "state", "delivery_status"]
    readonly_fields = ["state", "delivery_status"]

    def delivery_status(self, obj):
        if obj.pk:
            return ("Staged - delivery automation for this channel is not "
                    "wired into the campaign send sweep yet.")
        return ""
    delivery_status.short_description = "Status"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "channel":
            # Email is the campaign's built-in channel (template_version +
            # subject_override above); never a second email source.
            kwargs["queryset"] = db_field.remote_field.model.objects.exclude(kind="email")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class CampaignRecipientInline(admin.TabularInline):
    """Frozen audience snapshot + per-user send outcome (read-only)."""
    model = CampaignRecipient
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in CampaignRecipient._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "state", "template_version", "audience", "scheduled_at", "snapshot_at"]
    list_filter = ["state"]
    search_fields = ["name"]
    inlines = [CampaignActionInline, CampaignRecipientInline]

    fieldsets = [
        (None, {"fields": ["name", "state", "audience", "audience_size"]}),
        ("Email content", {
            "fields": ["template_version", "subject_override"],
            "description": "Executed by the campaign send sweep. Templates are "
                           "immutable versions: editing the template later "
                           "never changes this campaign.",
        }),
        ("Scheduling", {"fields": ["scheduled_at", "snapshot_at"]}),
        ("Results", {"fields": ["results_summary", "audit_links"], "classes": ["collapse"]}),
    ]
    readonly_fields = ["state", "snapshot_at", "audience_size", "results_summary", "audit_links"]

    actions = ["action_schedule", "action_send_batch"]

    # -- workspace helpers --------------------------------------------------
    def audience_size(self, obj):
        if not obj.audience_id:
            return "-"
        count = obj.audience.members().count()
        url = reverse("admin:campaigns_audience_change", args=[obj.audience_id])
        return format_html(
            '<a href="{}">{} current member(s)</a> (frozen at schedule time)',
            url, count)
    audience_size.short_description = "Audience size (live)"

    def results_summary(self, obj):
        m = CampaignMetrics.objects.filter(campaign_id=obj.pk).first()
        if m is None:
            return "No metrics yet -- schedule the campaign to freeze the audience."
        return "Total: {total} -- sent: {sent}, failed: {failed}, bounced: {bounced}".format(
            total=getattr(m, "total", "?"), sent=getattr(m, "sent", "?"),
            failed=getattr(m, "failed", "?"), bounced=getattr(m, "bounced", "?"))
    results_summary.short_description = "Campaign metrics"

    def audit_links(self, obj):
        if not obj.pk:
            return ""
        delivery_url = reverse("admin:emailing_delivery_changelist")
        tg_url = reverse("admin:advanced_growth_telegrammarketingdelivery_changelist")
        return format_html(
            'Email deliveries: <a href="{}">log</a> &nbsp;|&nbsp; '
            'Telegram marketing deliveries: <a href="{}">log</a>',
            delivery_url, tg_url)
    audit_links.short_description = "Delivery audit"

    # -- actions (behavior unchanged) ----------------------------------------
    @admin.action(description="Schedule (freeze audience snapshot)")
    def action_schedule(self, request, queryset):
        from .services.campaigns import schedule_campaign
        for c in queryset:
            schedule_campaign(c)

    @admin.action(description="Send a batch now")
    def action_send_batch(self, request, queryset):
        from .services.campaigns import schedule_campaign
        for c in queryset:
            if c.state == Campaign.State.DRAFT:
                schedule_campaign(c)
            enqueue_generic("campaign_send", {"campaign_id": str(c.id)},
                            idempotency_key=f"campaign_send:{c.id}:{__import__('uuid').uuid4().hex[:8]}")


@admin.register(Audience)
class AudienceAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]
