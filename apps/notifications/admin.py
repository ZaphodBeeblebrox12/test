"""
Notification admin configuration.
"""
from django.contrib import admin
from apps.notifications.models import Notification, EmailLog


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Admin interface for notifications."""

    list_display = [
        'title',
        'user',
        'notification_type',
        'is_urgent',
        'is_read',
        'created_at',
    ]
    list_filter = [
        'notification_type',
        'is_urgent',
        'is_read',
        'created_at',
    ]
    search_fields = [
        'title',
        'message',
        'user__username',
        'user__email',
    ]
    readonly_fields = [
        'created_at',
        'read_at',
    ]
    date_hierarchy = 'created_at'

    fieldsets = (
        (None, {
            'fields': ('user', 'notification_type', 'is_urgent')
        }),
        ('Content', {
            'fields': ('title', 'message', 'link', 'image')
        }),
        ('Status', {
            'fields': ('is_read', 'read_at')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    """Admin interface for email logs."""

    list_display = [
        'email',
        'template',
        'subject',
        'status',
        'sent_at',
        'created_at',
    ]
    list_filter = [
        'status',
        'template',
        'created_at',
    ]
    search_fields = [
        'email',
        'subject',
        'user__username',
    ]
    readonly_fields = ['created_at']
