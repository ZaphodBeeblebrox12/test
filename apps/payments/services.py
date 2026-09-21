"""P4/P5: shared payment lifecycle service.

The single authoritative path for "verified payment -> subscription".  Both
the customer-return confirm (P1) and the provider webhook (P4) call
`activate_paid_subscription` after their own verification.  All mutation flows
through the atomic conditional claim, so the two entry points are idempotent
regardless of arrival order.
"""
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from apps.subscriptions.models import Subscription, SubscriptionHistory
from .models import PaymentIntent


def interval_days_for(payment_intent):
    price_source = payment_intent.plan_price or payment_intent.geo_plan_price
    interval = getattr(price_source, "interval", "monthly")
    return {"monthly": 30, "quarterly": 90, "yearly": 365}.get(interval, 30)


def activate_paid_subscription(payment_intent) -> tuple[bool, object]:
    """Atomically claim the intent and activate the subscription.

    Returns (activated: bool, subscription|None).  Idempotent: a second call
    for an already-SUCCESS intent returns (False, existing_subscription).
    """
    claimed = PaymentIntent.objects.filter(
        pk=payment_intent.pk,
        status=PaymentIntent.Status.PENDING).update(
            status=PaymentIntent.Status.SUCCESS)
    if not claimed:
        existing = Subscription.objects.filter(
            user_id=payment_intent.user_id, plan=payment_intent.plan,
            is_active=True).first()
        return False, existing

    interval_days = interval_days_for(payment_intent)
    with transaction.atomic():
        subscription = Subscription.objects.create(
            user_id=payment_intent.user_id, plan=payment_intent.plan,
            plan_price=payment_intent.plan_price,
            geo_plan_price=payment_intent.geo_plan_price,
            status=Subscription.Status.ACTIVE, is_active=True,
            started_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=interval_days),
            price_cents=payment_intent.amount,
            price_currency=payment_intent.currency,
            payment_provider=payment_intent.provider,
            pricing_country=payment_intent.country,
            base_price_cents=payment_intent.base_amount_cents,
            discount_cents=payment_intent.coupon_discount_cents,
            coupon_code=payment_intent.applied_coupon_code)
        SubscriptionHistory.objects.create(
            subscription=subscription, user_id=payment_intent.user_id,
            event_type=SubscriptionHistory.EventType.CREATED,
            new_plan_id=payment_intent.plan_id,
            new_status=Subscription.Status.ACTIVE)
        from apps.growth.services.referrals import ReferralService
        ReferralService.complete_referral_on_purchase(
            user=subscription.user,
            purchase_amount_cents=payment_intent.amount,
            currency=payment_intent.currency,
            triggering_subscription=subscription,
        )
        from apps.growth.services.rewards import SubscriptionCreditService
        SubscriptionCreditService.apply_credit_to_subscription(
            user=subscription.user,
            subscription=subscription,
            plan_price_cents=(
                payment_intent.base_amount_cents or payment_intent.amount),
            plan_duration_days=interval_days,
        )
    from apps.events.models import record_event
    record_event(
        "purchase.completed",
        dedupe_key=f"purchase.completed:{payment_intent.pk}",
        user_id=payment_intent.user_id,
        object_ref=f"payment:{payment_intent.pk}",
        payload={"payment_intent_id": str(payment_intent.pk),
                 "subscription_id": str(subscription.pk),
                 "amount": payment_intent.amount, "currency": payment_intent.currency},
    )
    if getattr(payment_intent, "applied_coupon_code", ""):
        from apps.promotions.models import Coupon
        from apps.promotions.services.coupons import redeem_coupon
        coupon = Coupon.objects.filter(
            code__iexact=payment_intent.applied_coupon_code).first()
        created = redeem_coupon(coupon=coupon, user=payment_intent.user,
                          payment_intent=payment_intent, subscription=subscription,
                          discount_cents=payment_intent.coupon_discount_cents,
                          base_amount_cents=payment_intent.base_amount_cents,
                          final_amount_cents=payment_intent.amount,
                          currency=payment_intent.currency)
        if created:
            from apps.promotions.models import CouponRedemption
            from django.utils import timezone as _tz
            CouponRedemption.objects.filter(
               coupon=coupon, payment_intent=payment_intent).update(
                    finalized=True, finalized_at=_tz.now())

    return True, subscription
