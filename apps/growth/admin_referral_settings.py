
# Add to apps/growth/admin.py

from django.contrib import admin
from .models import ReferralSettings

@admin.register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    """
    Admin for referral program settings.

    Change reward amount here and it immediately applies to all calculations.
    """
    list_display = ['id', 'get_reward_amount', 'get_hold_duration', 'is_active', 'updated_at']

    def get_reward_amount(self, obj):
        """Display reward amount in dollars."""
        # Try common field names
        for field in ['reward_amount_cents', 'reward_cents', 'amount_cents']:
            if hasattr(obj, field):
                cents = getattr(obj, field)
                return f"${cents/100:.2f}" if cents else "$0.00"
        return "N/A"
    get_reward_amount.short_description = "Reward Amount"

    def get_hold_duration(self, obj):
        """Display hold duration."""
        for field in ['hold_duration_hours', 'hold_hours', 'pending_hours']:
            if hasattr(obj, field):
                hours = getattr(obj, field)
                return f"{hours}h ({hours/24:.1f} days)" if hours else "N/A"
        return "N/A"
    get_hold_duration.short_description = "Hold Duration"
