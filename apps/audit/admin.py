"""
Audit admin configuration.
"""
from django.contrib import admin

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Audit log admin."""

    list_display = ['action', 'user', 'object_type', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['action', 'user__username', 'object_type']
    readonly_fields = ['id', 'created_at']
    date_hierarchy = 'created_at'
