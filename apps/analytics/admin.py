from django.contrib import admin

from .models import SignupAttribution


@admin.register(SignupAttribution)
class SignupAttributionAdmin(admin.ModelAdmin):
    """First-touch attribution: read-only audit."""
    list_display = ["user", "utm_source", "utm_medium", "utm_campaign", "captured_at"]
    search_fields = ["user__username", "utm_campaign", "utm_source"]
    readonly_fields = [f.name for f in SignupAttribution._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
