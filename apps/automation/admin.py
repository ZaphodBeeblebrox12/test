from django.contrib import admin

from .models import AutomationRule, AutomationRun


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "trigger_event_type", "delay_minutes", "enabled", "created_at"]
    list_filter = ["trigger_event_type", "enabled"]
    search_fields = ["name"]


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    """Automation execution audit: read-only, no delete."""
    list_display = ["rule", "status", "user", "scheduled_at", "fired_at", "delivery"]
    list_filter = ["status", "rule"]
    search_fields = ["rule__name", "user__username"]
    date_hierarchy = "scheduled_at"
    readonly_fields = [f.name for f in AutomationRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
