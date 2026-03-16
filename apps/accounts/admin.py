"""
Accounts admin with Discord app configuration.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.urls import path
from django.http import HttpResponseRedirect
from django.contrib import messages

from apps.accounts.models import User, UserPreference, Profile, DiscordAppConfig


@admin.register(DiscordAppConfig)
class DiscordAppConfigAdmin(admin.ModelAdmin):
    """Admin for Discord OAuth2 app configuration."""

    list_display = ['name', 'client_id_masked', 'redirect_uri', 'is_active', 'updated_at']
    list_editable = ['is_active']
    fields = ['name', 'client_id', 'client_secret', 'redirect_uri', 'is_active']

    def client_id_masked(self, obj):
        if obj.client_id:
            return obj.client_id[:10] + "..." + obj.client_id[-4:]
        return "Not set"
    client_id_masked.short_description = "Client ID"

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['title'] = 'Discord App Configuration'
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """User admin with Discord fields."""

    list_display = [
        'username', 'email', 'first_name', 'last_name', 'role',
        'telegram_status', 'discord_status', 'is_active', 'date_joined'
    ]
    list_filter = ['role', 'is_active', 'is_staff', 'telegram_verified', 'discord_verified']
    search_fields = ['username', 'email', 'telegram_username', 'discord_username']

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Telegram', {
            'fields': ('telegram_id', 'telegram_username', 'telegram_verified'),
            'classes': ('collapse',),
        }),
        ('Discord', {
            'fields': ('discord_id', 'discord_username', 'discord_avatar', 'discord_verified'),
            'classes': ('collapse',),
        }),
        ('Profile', {
            'fields': ('bio', 'avatar'),
        }),
        ('Role & Status', {
            'fields': ('role', 'is_staff_approved', 'is_banned', 'ban_reason'),
        }),
    )

    def telegram_status(self, obj):
        if obj.telegram_verified:
            return format_html('<span style="color: green;">✓ @{}</span>', obj.telegram_username)
        return format_html('<span style="color: gray;">✗</span>')
    telegram_status.short_description = 'Telegram'

    def discord_status(self, obj):
        if obj.discord_verified:
            return format_html('<span style="color: #5865F2;">✓ {}</span>', obj.discord_username)
        return format_html('<span style="color: gray;">✗</span>')
    discord_status.short_description = 'Discord'


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'timezone', 'language', 'notifications_enabled']
    list_filter = ['language', 'notifications_enabled']
    search_fields = ['user__username']


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'timezone', 'language', 'created_at']
    search_fields = ['user__username']
