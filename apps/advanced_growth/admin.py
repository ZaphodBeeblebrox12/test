from django.contrib import admin

from .models import (
    Affiliate, AffiliateAttribution, AffiliateCommission, Announcement,
    AnnouncementExposure, CampaignAction, Channel, Experiment, ExperimentAssignment,
    ExperimentConversion, ExperimentVariant, TelegramMarketingDelivery)


class ReadOnlyMixin:
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Affiliate)
class AffiliateAdmin(admin.ModelAdmin):
    list_display = ["user", "code", "commission_percent", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["user__username", "code"]
    readonly_fields = ["id", "created_at"]


@admin.register(AffiliateAttribution)
class AffiliateAttributionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["affiliate", "user", "signed_up_at", "purchased_at"]
    search_fields = ["affiliate__code", "user__username"]


@admin.register(AffiliateCommission)
class AffiliateCommissionAdmin(admin.ModelAdmin):
    """Commission accounting: read-mostly, no delete (audit)."""
    list_display = ["affiliate", "user", "amount_cents", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["affiliate__code"]
    readonly_fields = ["id", "affiliate", "user", "payment_intent", "amount_cents",
                       "currency", "created_at"]
    actions = ["approve", "mark_payable", "mark_paid", "reverse"]

    def has_delete_permission(self, request, obj=None):
        return False

    def _move(self, queryset, status):
        from .services.growth import transition_commission
        for c in queryset:
            transition_commission(c, status)

    @admin.action(description="Approve selected commissions")
    def approve(self, request, queryset):
        self._move(queryset, AffiliateCommission.Status.APPROVED)

    @admin.action(description="Mark payable")
    def mark_payable(self, request, queryset):
        self._move(queryset, AffiliateCommission.Status.PAYABLE)

    @admin.action(description="Mark paid")
    def mark_paid(self, request, queryset):
        self._move(queryset, AffiliateCommission.Status.PAID)

    @admin.action(description="Reverse (refund/chargeback)")
    def reverse(self, request, queryset):
        self._move(queryset, AffiliateCommission.Status.REVERSED)


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "status"]
    list_filter = ["status"]
    search_fields = ["name", "key"]


@admin.register(ExperimentVariant)
class ExperimentVariantAdmin(admin.ModelAdmin):
    list_display = ["experiment", "key", "allocation_percent"]
    list_filter = ["experiment"]


@admin.register(ExperimentAssignment)
class ExperimentAssignmentAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["experiment", "user", "variant", "assigned_at"]
    list_filter = ["experiment", "variant"]


@admin.register(ExperimentConversion)
class ExperimentConversionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["assignment", "created_at"]


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ["kind", "name"]


@admin.register(CampaignAction)
class CampaignActionAdmin(admin.ModelAdmin):
    list_display = ["campaign", "channel", "state"]
    list_filter = ["channel", "state"]


@admin.register(TelegramMarketingDelivery)
class TelegramMarketingDeliveryAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["user", "state", "idempotency_key", "created_at"]
    list_filter = ["state"]


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "priority", "active", "starts_at", "ends_at"]
    list_filter = ["kind", "active"]
    search_fields = ["title"]


@admin.register(AnnouncementExposure)
class AnnouncementExposureAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["announcement", "user", "dismissed", "created_at"]
