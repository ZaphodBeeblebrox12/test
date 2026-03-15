"""
Admin configuration for accounts.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User, UserPreference, Profile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom User admin."""

    list_display = [
        'username', 'email', 'first_name', 'last_name', 'role',
        'telegram_username', 'telegram_verified', 'is_banned',
        'is_staff', 'date_joined', 'last_login'
    ]
    list_filter = [
        'role', 'is_banned', 'telegram_verified', 'is_staff',
        'is_superuser', 'is_active', 'date_joined'
    ]
    search_fields = ['username', 'email', 'telegram_username', 'first_name', 'last_name']
    readonly_fields = ['id', 'date_joined', 'last_login', 'banned_at']

    fieldsets = (
        (None, {'fields': ('id', 'username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email', 'bio', 'avatar')}),
        (_('Telegram'), {'fields': ('telegram_id', 'telegram_username', 'telegram_verified')}),
        (_('Permissions'), {
            'fields': ('role', 'is_staff', 'is_staff_approved', 'is_superuser', 'is_active'),
        }),
        (_('Ban status'), {'fields': ('is_banned', 'ban_reason', 'banned_at')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    actions = ['ban_users', 'unban_users', 'approve_staff']

    @admin.action(description='Ban selected users')
    def ban_users(self, request, queryset):
        for user in queryset:
            user.ban()

    @admin.action(description='Unban selected users')
    def unban_users(self, request, queryset):
        for user in queryset:
            user.unban()

    @admin.action(description='Approve staff role')
    def approve_staff(self, request, queryset):
        for user in queryset.filter(role='staff'):
            user.approve_staff()


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    """User preference admin."""

    list_display = ['user', 'timezone', 'language', 'notifications_enabled']
    list_filter = ['language', 'notifications_enabled']
    search_fields = ['user__username', 'user__email']


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """Profile admin (legacy)."""

    list_display = ['user', 'timezone', 'language', 'created_at']
    search_fields = ['user__username']
