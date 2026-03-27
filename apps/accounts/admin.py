"""
Admin configuration for accounts app with nickname support.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """User admin with nickname support."""

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Profile', {'fields': ('nickname',)}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Profile', {
            'classes': ('wide',),
            'fields': ('nickname',),
        }),
    )

    list_display = ('username', 'email', 'nickname', 'first_name', 'last_name', 'is_staff')
    list_editable = ('nickname',)
    search_fields = ('username', 'first_name', 'last_name', 'email', 'nickname')
