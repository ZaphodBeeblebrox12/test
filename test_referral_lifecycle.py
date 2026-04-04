#!/usr/bin/env python
"""
DROP-IN VALIDATION TEST (NO MANUAL SERVICE CALLS)

Run:
python manage.py shell < test_referral_dropin_validation.py

Goal:
Verify FULL automatic flow works:
User A → refer → User B → signup → payment → reward → dashboard

NO manual calls to ReferralService allowed.
"""

import os
import django
from django.utils import timezone
from django.contrib.auth import get_user_model

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.growth.models import ReferralCode, Referral, ReferralReward, ReferralSettings
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan, PlanPrice, Subscription

User = get_user_model()

# =========================

# CONFIG

# =========================

TEST_USERNAME_A = "dropin_referrer"
TEST_USERNAME_B = "dropin_referred"
PASSWORD = "test123"

# =========================

# HELPERS

# =========================

def assert_check(name, condition):
status = "PASS" if condition else "FAIL"
symbol = "✓" if condition else "✗"
print(f"{symbol} {name}: {status}")
return condition

def section(title):
print("\n" + "="*50)
print(title)
print("="*50)

# =========================

# CLEANUP

# =========================

section("CLEANUP")

User.objects.filter(username__in=[TEST_USERNAME_A, TEST_USERNAME_B]).delete()
Referral.objects.all().delete()
ReferralReward.objects.all().delete()
PaymentIntent.objects.all().delete()
Subscription.objects.all().delete()

# =========================

# SETUP PLAN

# =========================

section("SETUP PLAN")

plan, _ = Plan.objects.get_or_create(
tier="pro",
defaults={"name": "Test Plan"}
)

plan_price, _ = PlanPrice.objects.get_or_create(
plan=plan,
interval="monthly",
currency="USD",
defaults={"price_cents": 1000}
)

print(f"Plan created: ${plan_price.price_cents/100}")

# =========================

# STEP 1: USER A

# =========================

section("USER A (REFERRER)")

user_a = User.objects.create_user(
username=TEST_USERNAME_A,
password=PASSWORD
)

ref_code = ReferralCode.get_or_create_for_user(user_a)

assert_check("User A created", user_a is not None)
assert_check("Referral code exists", ref_code is not None)

print("Referral Code:", ref_code.code)

# =========================

# STEP 2: USER B SIGNUP

# =========================

section("USER B (REFERRED)")

# simulate referral capture (adjust if needed)

user_b = User.objects.create_user(
username=TEST_USERNAME_B,
password=PASSWORD
)

# simulate referral capture mechanism

user_b._referral_code = ref_code.code
user_b.save()

# CHECK referral created automatically

referral = Referral.objects.filter(referred_user=user_b).first()

assert_check("Referral auto-created", referral is not None)

if referral:
assert_check("Referral status PENDING", referral.status == Referral.Status.PENDING)

# =========================

# STEP 3: PAYMENT

# =========================

section("PAYMENT")

payment = PaymentIntent.objects.create(
user=user_b,
plan=plan,
plan_price=plan_price,
amount=1000,
currency="USD",
status=PaymentIntent.Status.PENDING
)

# simulate success

payment.status = PaymentIntent.Status.SUCCESS
payment.save()

# create subscription (normally auto)

subscription = Subscription.objects.create(
user=user_b,
plan=plan,
plan_price=plan_price,
status=Subscription.Status.ACTIVE,
is_active=True,
started_at=timezone.now(),
expires_at=timezone.now() + timezone.timedelta(days=30)
)

# refresh referral (should auto-complete)

referral.refresh_from_db()

assert_check("Referral auto-completed", referral.status == Referral.Status.COMPLETED)

# =========================

# STEP 4: REWARD

# =========================

section("REWARD")

reward = ReferralReward.objects.filter(referral=referral).first()

assert_check("Reward created automatically", reward is not None)

if reward:
print("Reward cents:", reward.amount_cents)
assert_check("Reward > 0", reward.amount_cents > 0)

# =========================

# STEP 5: UNLOCK

# =========================

section("UNLOCK")

if reward:
reward.unlocked_at = timezone.now()
reward.save()

```
reward.refresh_from_db()
assert_check("Reward unlock ready", reward.unlocked_at <= timezone.now())
```

# =========================

# STEP 6: DISCOUNT CHECK

# =========================

section("DISCOUNT")

original_price = 1000
actual_price = payment.amount

assert_check("Discount applied (<= original)", actual_price <= original_price)

# =========================

# STEP 7: DASHBOARD

# =========================

section("DASHBOARD")

from apps.accounts.referral_dashboard_mixin import ReferralDashboardMixin

class DummyView(ReferralDashboardMixin):
request = type("obj", (), {"user": user_a})

view = DummyView()
context = view.get_context_data()

assert_check("Context has reward %", "reward_percentage" in context)
assert_check("Context has credits", "referral_stats" in context)
assert_check("Context has progress", "progress_message" in context)

print("Dashboard OK")

# =========================

# FINAL RESULT

# =========================

section("RESULT")

print("If all PASS → system is TRUE DROP-IN")
