from django.contrib import admin

from .models import RiskSignal


@admin.register(RiskSignal)
class RiskSignalAdmin(admin.ModelAdmin):
    list_display = ("signal_type", "user", "severity", "source",
                    "disposition", "created_at", "reviewed_by")
    list_filter = ("signal_type", "severity", "disposition")
    search_fields = ("user__username", "subject_hash")
    readonly_fields = ("id", "user", "signal_type", "severity",
                       "subject_hash", "detail", "source", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False
