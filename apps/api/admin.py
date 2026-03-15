"""
API admin configuration.
"""
from django.contrib import admin

from apps.api.models import APIKey, APIRequestLog


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    """API key admin."""

    list_display = ['name', 'user', 'key_prefix', 'is_active', 'last_used_at', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'user__username', 'key_prefix']
    readonly_fields = ['id', 'key_hash', 'key_prefix', 'created_at']


@admin.register(APIRequestLog)
class APIRequestLogAdmin(admin.ModelAdmin):
    """API request log admin."""

    list_display = ['endpoint', 'method', 'status_code', 'user', 'api_key', 'created_at']
    list_filter = ['method', 'status_code', 'created_at']
    search_fields = ['endpoint', 'user__username']
    readonly_fields = ['id', 'created_at']
