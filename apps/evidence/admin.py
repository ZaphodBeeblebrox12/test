from django.contrib import admin

from .models import (
    CancellationRequest, CheckoutEvidence, MembershipConfirmation,
    PaymentAuthentication, PlanRefundTerms)


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(CheckoutEvidence)
class CheckoutEvidenceAdmin(_ReadOnly):
    list_display = ("payment_intent", "accepted_at", "ip_address",
                    "device_fingerprint", "checkout_version")
    search_fields = ("payment_intent__id", "ip_address", "device_fingerprint")
    list_filter = ("checkout_version", "fingerprint_version")


@admin.register(PaymentAuthentication)
class PaymentAuthenticationAdmin(_ReadOnly):
    list_display = ("payment_intent", "three_ds_result", "card_brand",
                    "card_last4", "card_fingerprint", "backfilled_at")
    search_fields = ("payment_intent__id", "card_fingerprint", "network_txn_id")
    list_filter = ("three_ds_result", "card_brand", "wallet_type")


@admin.register(CancellationRequest)
class CancellationRequestAdmin(_ReadOnly):
    list_display = ("user", "subscription", "requested_at", "channel",
                    "effective_at", "actor")
    search_fields = ("user__username", "user__email", "note")
    list_filter = ("channel",)


@admin.register(MembershipConfirmation)
class MembershipConfirmationAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "external_id", "confirmed_at",
                    "last_seen_at")
    search_fields = ("user__username", "external_id")
    list_filter = ("platform",)
    readonly_fields = ("id", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlanRefundTerms)
class PlanRefundTermsAdmin(admin.ModelAdmin):
    list_display = ("plan", "refund_window_days", "trial_refund_window_days",
                    "cancellation_deadline_hours", "updated_at")
    search_fields = ("plan__name",)

    def has_delete_permission(self, request, obj=None):
        return False  # terms history is referenced by snapshots
