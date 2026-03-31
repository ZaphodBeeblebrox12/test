"""
Add this to apps/growth/models.py if ReferralSettings doesn't exist
"""

class ReferralSettings(models.Model):
    """Global settings for referral program."""

    reward_amount_cents = models.PositiveIntegerField(
        default=1000,
        help_text="Referral reward amount in cents (e.g., 1000 for $10.00)"
    )
    hold_duration_hours = models.PositiveIntegerField(
        default=72,
        help_text="Hours to hold rewards before unlocking (fraud protection)"
    )
    max_rewards_per_user = models.PositiveIntegerField(
        default=0,
        help_text="Max rewards per referrer (0 = unlimited)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether referral program is active"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Referral Settings"
        verbose_name_plural = "Referral Settings"

    def __str__(self):
        return f"Referral Settings (${self.reward_amount_cents/100:.2f} reward)"

    @classmethod
    def get_settings(cls):
        """Get or create singleton settings."""
        settings, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'reward_amount_cents': 1000,
                'hold_duration_hours': 72
            }
        )
        return settings
