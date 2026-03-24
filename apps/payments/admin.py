"""
Payment admin configuration.
"""
from django.contrib import admin

from .models import PaymentIntent


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
    search_fields = ["user__username", "user__email", "plan__name"]
    readonly_fields = ["id", "created_at", "updated_at"]

    def amount_display(self, obj):
        return f"{obj.currency} {obj.amount_dollars:.2f}"
    amount_display.short_description = "Amount"
