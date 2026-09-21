"""Durable jobs admin: operational / read-mostly."""
from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """Read-mostly view of the durable-jobs queue.  Do not hand-edit job
    state here (that would corrupt the queue); this is for inspection."""

    list_display = [
        "id", "kind", "status", "attempts", "max_attempts",
        "next_attempt_at", "locked_at", "created_at",
    ]
    list_filter = ["kind", "status", "created_at"]
    search_fields = ["kind", "idempotency_key", "payload"]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    # Queue state is owned by the worker; admins inspect, not mutate.
    readonly_fields = [f.name for f in Job._meta.fields]

    def has_delete_permission(self, request, obj=None):
        # Deleting a job loses operational/audit trail of the queue.
        return False

    def has_add_permission(self, request):
        # Jobs are enqueued by code, not created by hand.
        return False
