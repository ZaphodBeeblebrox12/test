"""
Payment admin configuration.
"""
from django.contrib import admin

from .models import PaymentIntent, WebhookEvent


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "plan",
        "amount_display",
        "currency",
        "provider",
        "status",
        "country",
        "created_at"
    ]
    list_filter = ["status", "provider", "currency", "country", "created_at"]
    list_select_related = ["user", "plan"]
    autocomplete_fields = ["user", "plan"]
    search_fields = ["user__username", "user__email", "plan__name"]
    # PaymentIntent is financial/audit data: ALL fields read-only.  Payment
    # state is set only by the provider-verification flow (P1); an admin must
    # never mark paid/failed or edit amount/provider/snapshots by hand.
    readonly_fields = [f.name for f in PaymentIntent._meta.fields]

    def has_delete_permission(self, request, obj=None):
        # Financial evidence; never delete.
        return False

    def amount_display(self, obj):
        return f"{obj.currency} {obj.amount_dollars:.2f}"
    amount_display.short_description = "Amount"

@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Received provider webhook events: read-only audit, no add/delete."""
    list_display = ["provider", "event_type", "status", "provider_event_id", "received_at", "processed_at"]
    list_filter = ["provider", "status", "event_type"]
    search_fields = ["provider_event_id", "event_type"]
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
