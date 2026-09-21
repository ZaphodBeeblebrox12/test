from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Durable domain events: read-only audit, no add/change/delete."""
    list_display = ["event_type", "user", "object_ref", "occurred_at"]
    list_filter = ["event_type", "occurred_at"]
    search_fields = ["dedupe_key", "object_ref", "event_type"]
    date_hierarchy = "occurred_at"
    readonly_fields = [f.name for f in Event._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
