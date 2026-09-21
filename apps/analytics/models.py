"""G5: signup attribution (first-touch UTM) + attribution service.

Attribution model: a user has ONE SignupAttribution (captured at signup/first
UTM touch).  Campaign performance uses LAST-TOUCH: the most recent
marketing-email Delivery to the user before the purchase "gets credit".
Revenue is the sum of confirmed purchase Events (authoritative), optionally
per attributed campaign.
"""
import uuid

from django.conf import settings
from django.db import models


class SignupAttribution(models.Model):
    """First-touch UTM + referring campaign captured at signup."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="signup_attribution")
    utm_source = models.CharField(max_length=100, blank=True, default="")
    utm_medium = models.CharField(max_length=100, blank=True, default="")
    utm_campaign = models.CharField(max_length=100, blank=True, default="")
    utm_term = models.CharField(max_length=100, blank=True, default="")
    utm_content = models.CharField(max_length=100, blank=True, default="")
    referring_campaign = models.ForeignKey(
        "campaigns.Campaign", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="attributed_signups",
        help_text="The G2 campaign whose link brought this user (if known).")
    captured_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"attr:{self.user_id}"
