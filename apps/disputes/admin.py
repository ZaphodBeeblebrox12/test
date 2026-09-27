from django.contrib import admin

from .models import Dispute, DisputeEvent, DisputeEvidencePackage


class DisputeEventInline(admin.TabularInline):
    model = DisputeEvent
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in DisputeEvent._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("provider_dispute_id", "payment_intent",
                    "reason_category", "amount_cents", "currency", "status",
                    "opened_at", "evidence_due_at", "assigned_to")
    list_filter = ("status", "reason_category", "provider", "network")
    search_fields = ("provider_dispute_id", "payment_intent__id",
                     "payment_intent__user__username")
    inlines = [DisputeEventInline]

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(DisputeEvidencePackage)
class PackageAdmin(admin.ModelAdmin):
    list_display = ("dispute", "generator_version", "generated_at",
                    "submitted_at", "outcome")
    search_fields = ("dispute__provider_dispute_id",)
    readonly_fields = [f.name for f in DisputeEvidencePackage._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
