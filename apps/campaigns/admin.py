from django.contrib import admin

from .models import Audience, Campaign, CampaignMetrics, CampaignRecipient


class CampaignRecipientInline(admin.TabularInline):
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
    readonly_fields = ["snapshot_at"]
    inlines = [CampaignRecipientInline]

    actions = ["action_schedule", "action_send_batch"]

    @admin.action(description="Schedule (freeze audience snapshot)")
    def action_schedule(self, request, queryset):
        from .services.campaigns import schedule_campaign
        for c in queryset:
            schedule_campaign(c)

    @admin.action(description="Send a batch now")
    def action_send_batch(self, request, queryset):
        from apps.jobs.enqueue import enqueue_generic
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


@admin.register(CampaignRecipient)
class CampaignRecipientAdmin(admin.ModelAdmin):
    list_display = ["campaign", "email", "status", "delivery"]
    list_filter = ["status", "campaign"]
    search_fields = ["email"]
    readonly_fields = [f.name for f in CampaignRecipient._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CampaignMetrics)
class CampaignMetricsAdmin(admin.ModelAdmin):
    list_display = ["campaign", "total", "sent", "failed", "bounced", "updated_at"]
    readonly_fields = [f.name for f in CampaignMetrics._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
