"""
System settings admin.
"""
from django.contrib import admin

from apps.system_settings.models import SystemSetting


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    """System setting admin."""

    list_display = ['key', 'value', 'is_public', 'updated_at']
    list_filter = ['is_public']
    search_fields = ['key', 'value', 'description']
    readonly_fields = ['created_at', 'updated_at']
