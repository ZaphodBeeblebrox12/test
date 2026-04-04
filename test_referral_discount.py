#!/usr/bin/env python
"""
Fully isolated test for referral checkout discount (Option A).
Run with: python manage.py shell < test_referral_discount.py
"""

import sys
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction
from decimal import Decimal

User = get_user_model()

from apps.growth.models import Referral, ReferralCode, ReferralSettings, ReferralReward
from apps.growth.services import ReferralService
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan, PlanPrice, Subscription

def full_cleanup():
    test_usernames = ["referrer_test", "referee_test"]
    for username in test_usernames:
        try:
            user = User.objects.get(username=username)
            Subscription.objects.filter(user=user).delete()
            PaymentIntent.objects.filter(user=user).delete()
            Referral.objects.filter(referrer=user).delete()
            Referral.objects.filter(referred_user=user).delete()
            ReferralReward.objects.filter(referrer=user).delete()
            user.delete()
        except User.DoesNotExist:
            pass
    print("✓ Full cleanup completed")

def create_test_plan():
    plan, _ = Plan.objects.get_or_create(
        tier="basic",
        defaults={
            "name": "Basic",
            "is_active": True,
            "max_projects": 5,
            "max_storage_mb": 100,
            "api_calls_per_day": 1000,
            "display_order": 10,
        }
    )
    plan_price, _ = PlanPrice.objects.get_or_create(
        plan=plan,
        interval=PlanPrice.Interval.MONTHLY,
        currency="USD",
        defaults={"price_cents": 1000, "is_active": True}
    )
    if plan_price.price_cents != 1000:
        plan_price.price_cents = 1000
        plan_price.save()
    return plan, plan_price

def ensure_settings():
    settings = ReferralSettings.get_settings()
    settings.referee_discount_percent = 20
    settings.default_reward_percentage = Decimal("20.00")
    settings.save()
    print(f"✓ Settings: discount {settings.referee_discount_percent}%, reward {settings.default_reward_percentage}%")

def run_test():
    print("=" * 60)
    print("Testing Referral Checkout Discount (Option A)")
    print("=" * 60)

    full_cleanup()
    ensure_settings()
    plan, plan_price = create_test_plan()
    print(f"✓ Test plan: {plan.name} @ ${plan_price.price_cents/100:.2f}")

    referrer = User.objects.create_user(username="referrer_test", email="referrer@test.com", password="pass")
    referee = User.objects.create_user(username="referee_test", email="referee@test.com", password="pass")
    print(f"✓ Created users: {referrer.username}, {referee.username}")

    referral_code, _ = ReferralCode.objects.get_or_create(user=referrer, defaults={"code": "TEST1234"})
    referral_code.code = "TEST1234"
    referral_code.save()

    referral = ReferralService.record_referral_signup(referee, "TEST1234")
    assert referral is not None, "Referral not created"
    assert referral.status == Referral.Status.PENDING
    assert referral.discount_used is False
    print(f"✓ Referral created: {referrer.username} -> {referee.username} (status={referral.status})")

    discount_info = ReferralService.get_checkout_discount(referee, amount_cents=1000)
    assert discount_info["has_discount"] is True
    assert discount_info["discount_percent"] == 20
    assert discount_info["final_amount_cents"] == 800
    assert discount_info["referral"] is not None
    print(f"✓ Discount applied: ${discount_info['final_amount_cents']/100:.2f} (20% off)")

    with transaction.atomic():
        payment_intent = PaymentIntent.objects.create(
            user=referee,
            plan=plan,
            plan_price=plan_price,
            amount=discount_info["final_amount_cents"],
            currency="USD",
            provider="stripe",
            status=PaymentIntent.Status.PENDING,
            applied_referral_discount=discount_info["referral"],
        )
    print(f"✓ PaymentIntent created: amount=${payment_intent.amount/100:.2f}")

    with transaction.atomic():
        locked_referral = Referral.objects.select_for_update().get(id=referral.id)
        if payment_intent.status != PaymentIntent.Status.SUCCESS:
            payment_intent.status = PaymentIntent.Status.SUCCESS
            payment_intent.save()
        if not locked_referral.discount_used:
            locked_referral.discount_used = True
            locked_referral.save(update_fields=["discount_used"])

        expires_at = timezone.now() + timezone.timedelta(days=30)
        subscription = Subscription.objects.create(
            user=referee,
            plan=plan,
            plan_price=plan_price,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider="stripe",
        )

        ReferralService.complete_referral_on_purchase(
            user=referee,
            purchase_amount_cents=payment_intent.amount,
            currency="USD",
            triggering_subscription=subscription,
        )

    referral.refresh_from_db()
    assert referral.discount_used is True
    assert subscription.is_active is True

    reward = ReferralReward.objects.filter(referral=referral).first()
    assert reward is not None, "Referrer reward not created"
    assert reward.amount_cents == 160, f"Expected 160 cents, got {reward.amount_cents}"
    print(f"✓ Referrer reward: ${reward.amount_cents/100:.2f} (20% of ${payment_intent.amount/100:.2f})")

    discount_info2 = ReferralService.get_checkout_discount(referee, amount_cents=1000)
    assert discount_info2["has_discount"] is False
    print("✓ Discount reuse blocked")

    full_cleanup()
    print("\n" + "=" * 60)
    print("✅ TEST PASSED – Both referee discount and referrer reward work correctly")
    print("=" * 60)

if __name__ == "__main__":
    try:
        run_test()
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)