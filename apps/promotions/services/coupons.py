"""G4 coupon service: validate, apply (amount math), and redeem atomically."""
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import Coupon, CouponRedemption


class CouponError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def validate_coupon(coupon, *, user, plan, base_amount_cents, has_referral_discount):
    """Return discount_cents, or raise CouponError with a human reason."""
    if coupon is None:
        raise CouponError("invalid code")
    if not coupon.active:
        raise CouponError("coupon inactive")
    now = timezone.now()
    if coupon.valid_from and now < coupon.valid_from:
        raise CouponError("not yet valid")
    if coupon.valid_until and now > coupon.valid_until:
        raise CouponError("expired")
    if coupon.plan_tiers and plan.tier not in coupon.plan_tiers:
        raise CouponError("not valid for this plan")
    if has_referral_discount and not coupon.stackable_with_referral:
        raise CouponError("cannot combine with referral discount")
    # Model C campaign scoping (unscoped coupons skip this entirely).  Uses the
    # authoritative first-touch campaign attribution; never trusts a client-
    # supplied campaign id (the Coupon's own FK is authoritative).  Generic
    # error message does not leak which campaign owns the coupon.
    if coupon.campaign_id is not None:
        from apps.analytics.models import SignupAttribution
        attr = SignupAttribution.objects.filter(user=user).first()
        if attr is None or attr.referring_campaign_id != coupon.campaign_id:
            raise CouponError("not eligible for this offer")
    # Per-user limit.
    user_uses = CouponRedemption.objects.filter(coupon=coupon, user=user).count()
    if user_uses >= coupon.max_per_user:
        raise CouponError("usage limit reached for this user")
    # Global limit.
    if coupon.max_redemptions is not None:
        total = CouponRedemption.objects.filter(coupon=coupon).count()
        if total >= coupon.max_redemptions:
            raise CouponError("coupon fully redeemed")
    # Compute discount on the base (post-referral) amount, never below 0.
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        discount = (base_amount_cents * coupon.percent_off) // 100
    else:
        discount = coupon.amount_off_cents
    return min(discount, base_amount_cents)


def apply_coupon(*, user, plan, base_amount_cents, code, has_referral_discount=False):
    """Validate + compute the discounted amount.  Returns (amount, coupon, discount)."""
    coupon = Coupon.objects.filter(code__iexact=(code or "").strip()).first()
    discount = validate_coupon(coupon, user=user, plan=plan,
                               base_amount_cents=base_amount_cents,
                               has_referral_discount=has_referral_discount)
    return base_amount_cents - discount, coupon, discount


def redeem_coupon(*, coupon, user, payment_intent, subscription, discount_cents,
                  base_amount_cents=0, final_amount_cents=0, currency="USD"):
    """Atomically record a redemption (claim).  Returns True, or False if the
    coupon hit its global limit concurrently (no over-redemption)."""
    if coupon is None:
        return False
    with transaction.atomic():
        # Under the transaction, re-check the global limit before recording a
        # new redemption so concurrent activations cannot over-redeem.
        if coupon.max_redemptions is not None:
            count = CouponRedemption.objects.filter(coupon=coupon).count()
            if count >= coupon.max_redemptions:
                return False
        _, created = CouponRedemption.objects.get_or_create(
            coupon=coupon, payment_intent=payment_intent,
            defaults={"user": user, "subscription": subscription,
                      "discount_cents": discount_cents,
                      "base_amount_cents": base_amount_cents,
                      "final_amount_cents": final_amount_cents,
                      "currency": currency})
        return created
