"""G4: coupon/promo codes with eligibility, stacking, usage limits.

A Coupon is validated at checkout; the discount is applied to the
PaymentIntent amount (after any referral discount) and snapshotted for
provenance (which code, how much).  Redemption is recorded on successful
activation via an atomic claim (no over-redemption under concurrency).
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Coupon(models.Model):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percent off"
        FIXED = "fixed", "Fixed amount off (cents)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices)
    percent_off = models.PositiveIntegerField(
        null=True, blank=True, help_text="0-100. Used when discount_type=percent.")
    amount_off_cents = models.PositiveIntegerField(
        null=True, blank=True, help_text="Used when discount_type=fixed.")
    currency = models.CharField(max_length=3, default="USD")
    # Eligibility window.
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    # Usage limits.
    max_redemptions = models.PositiveIntegerField(null=True, blank=True,
                                                  help_text="Null = unlimited.")
    max_per_user = models.PositiveIntegerField(default=1)
    # G6 Model C: OPTIONAL campaign scoping.  NULL = unscoped (existing behavior,
    # anyone may redeem under normal rules).  SET = only users whose authoritative
    # SignupAttribution.referring_campaign is this campaign may redeem.
    # SET_NULL so deleting a campaign never invalidates historical coupons/redemptions.
    campaign = models.ForeignKey(
        "campaigns.Campaign", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="coupons", db_index=True)
    # Eligibility scope: which plan tiers this applies to (empty = all).
    plan_tiers = models.JSONField(default=list, blank=True,
                                  help_text='e.g. ["pro","enterprise"]. Empty = all tiers.')
    # Stacking: may this combine with a referral discount?
    stackable_with_referral = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.discount_type == self.DiscountType.PERCENT:
            if self.percent_off is None or not (0 < self.percent_off <= 100):
                raise ValidationError({"percent_off": "Percent must be 1-100."})
        if self.discount_type == self.DiscountType.FIXED:
            if not self.amount_off_cents:
                raise ValidationError({"amount_off_cents": "Fixed amount required."})

    def __str__(self):
        return self.code


class CouponRedemption(models.Model):
    """One use of a coupon by one user, on one PaymentIntent/Subscription.

    Created at successful activation (atomic claim on coupon + count) so
    max_redemptions and max_per_user are enforced under concurrency."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="coupon_redemptions")
    payment_intent = models.ForeignKey("payments.PaymentIntent", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="coupon_redemptions")
    subscription = models.ForeignKey("subscriptions.Subscription", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="coupon_redemptions")
    # Provenance: base -> discount -> final (auditable, refund-ready).
    base_amount_cents = models.PositiveIntegerField(default=0)
    discount_cents = models.PositiveIntegerField(default=0)
    final_amount_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    # The redemption is finalized once the payment is confirmed (activation).
    finalized = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("coupon", "payment_intent")]

    def __str__(self):
        return f"{self.coupon.code}:{self.user}"
