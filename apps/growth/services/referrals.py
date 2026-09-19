"""Referrals domain — split from apps/growth/services.py (mechanical, no behavior change)."""
import logging
from typing import Optional, Tuple, List
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from django.conf import settings

from apps.accounts.models import User

from apps.subscriptions.api import (
    create_gift_subscription as api_create_gift_subscription,
    extend_subscription_with_gift,
    create_subscription_from_gift,
    GiftAttribution,
    get_active_subscription,
    has_active_subscription,
    get_gift_by_code,
    get_gift_by_id,
)

from ..models import (
    Referral,
    ReferralCode,
    ReferralSettings,
    ReferralReward,
)

# ============================================================================
# REFERRAL SERVICE (Purchase Completion)
# ============================================================================


# --- cross-domain dependency (verified): ReferralService pays rewards ---
from .rewards import (
    UserRewardBalance,
    ReferralRewardService,
    SubscriptionCreditService,
)

class ReferralService:
    """
    Service for handling referral tracking and reward creation.

    Phase 4 (Viral Mode) Features:
    - Referral can be applied at signup OR before first paid purchase
    - Subscription-based validation (canonical business state)
    - Circular referrals blocked (no reward, no extra state)
    - Rewards delayed 72 hours before unlock
    - Refunds block reward unlock via explicit subscription link
    """

    @staticmethod
    def get_code_by_string(code: str) -> Optional[ReferralCode]:
        """Lookup referral code by string (case-insensitive)."""
        if not code:
            return None
        try:
            return ReferralCode.objects.select_related("user").get(
                code=code.upper().strip()
            )
        except ReferralCode.DoesNotExist:
            return None

    @classmethod
    def _has_any_successful_paid_subscription(cls, user: User) -> bool:
        """
        Check if user has EVER had a successful paid subscription.
        This is the canonical check for "first purchase" eligibility.

        Uses Subscription model as source of truth.

        ASSUMPTION: 
        - Paid subscriptions have payment_provider set (Stripe/Razorpay)
        - Gift subscriptions have payment_provider = None
        - If your model has an explicit `is_paid` or `source` field, use that instead

        TODO: If you add an explicit `is_paid` or `source` field to Subscription,
        update this method to use that instead of payment_provider__isnull.
        """
        from apps.subscriptions.models import Subscription
        return Subscription.objects.filter(
            user=user,
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.EXPIRED],
            # ASSUMPTION: payment_provider indicates paid vs gift
            # Paid = Stripe/Razorpay, Gift = None
            payment_provider__isnull=False
        ).exists()

    @classmethod
    def can_apply_referral(cls, user: User) -> bool:
        """
        Check if user can apply a referral code.

        Returns True if:
        - User does not already have a referrer
        - User has NEVER had a successful paid subscription
        """
        # Check if user already has a referrer (using explicit query)
        has_referrer = Referral.objects.filter(referred_user=user).exists()
        if has_referrer:
            return False

        # Check if user has EVER had a successful paid subscription
        if cls._has_any_successful_paid_subscription(user):
            return False

        return True

    @classmethod
    def _detect_fraud_pattern(cls, referrer: User, referred_user: User) -> bool:
        """
        Detect suspicious patterns that might indicate fraud.
        Currently only logs warnings - does not block.

        Patterns checked:
        - Multiple referrals from same domain in short time
        - Self-referral attempts (should be blocked by constraint)
        """
        # Check for repeated domain pattern
        if referrer.email and referred_user.email:
            referrer_domain = referrer.email.split('@')[-1].lower()
            referred_domain = referred_user.email.split('@')[-1].lower()

            # Same domain referrals (not necessarily fraud, but worth logging)
            if referrer_domain == referred_domain:
                # Count recent same-domain referrals by this referrer
                recent_count = Referral.objects.filter(
                    referrer=referrer,
                    created_at__gte=timezone.now() - timezone.timedelta(days=7)
                ).count()

                if recent_count > 5:
                    logger.warning(
                        f"Potential referral fraud: referrer={referrer.id} "
                        f"has {recent_count} recent referrals, "
                        f"including same-domain user {referred_user.id}"
                    )
                    return True

        return False

    @classmethod
    @transaction.atomic
    def record_referral_signup(cls, referred_user: User, code: str) -> Optional[Referral]:
        """
        Record that a user signed up with a referral code.
        Can be called at signup OR before first paid purchase.

        Returns None if:
        - Code is invalid
        - User already has a referrer
        - User has EVER had a successful paid subscription
        - Self-referral attempt
        """
        referral_code = cls.get_code_by_string(code)

        if not referral_code:
            logger.info(f"Invalid referral code used: {code}")
            return None

        referrer = referral_code.user

        # Prevent self-referral
        if referrer.id == referred_user.id:
            logger.warning(f"Self-referral attempt by user {referred_user.id}")
            return None

        # Check if referred_user already has a referral record (using explicit query)
        existing_referral = Referral.objects.filter(referred_user=referred_user).first()
        if existing_referral:
            logger.info(f"User {referred_user.id} already has referral record")
            return existing_referral

        # Check if user has EVER had a successful paid subscription
        if cls._has_any_successful_paid_subscription(referred_user):
            logger.info(f"User {referred_user.id} already has paid subscription history, cannot apply referral")
            return None

        # Fraud detection (log only)
        cls._detect_fraud_pattern(referrer, referred_user)

        # Create referral record (starts as pending - will complete on purchase)
        referral = Referral.objects.create(
            referrer=referrer,
            referred_user=referred_user,
            status=Referral.Status.PENDING
        )

        logger.info(f"Referral recorded (pending): {referrer.id} -> {referred_user.id}")
        return referral

    @classmethod
    def _is_circular_referral(cls, referral: Referral) -> bool:
        """
        Check if this is a circular referral (A refers B, B refers A).
        Uses explicit DB query instead of attribute chaining.
        """
        referrer = referral.referrer
        referred_user = referral.referred_user

        # Check if referrer was referred by the referred user
        is_circular = Referral.objects.filter(
            referrer=referred_user,
            referred_user=referrer
        ).exists()

        return is_circular

    @classmethod
    @transaction.atomic
    def complete_referral_on_purchase(
        cls,
        user: User,
        purchase_amount_cents: int = 0,
        currency: str = "USD",
        triggering_subscription=None
    ) -> Optional[Referral]:
        """
        Mark a user's referral as completed after successful paid purchase.
        Also creates reward if applicable (delayed unlock).

        Args:
            user: User who just made a purchase
            purchase_amount_cents: Amount of the purchase (for reward calculation)
            currency: Currency of the purchase
            triggering_subscription: The subscription that triggered this completion

        Returns:
            Referral object if completed, None if no pending referral or already completed
        """
        try:
            # Use select_for_update to prevent race conditions
            referral = Referral.objects.select_for_update().get(
                referred_user=user,
                status=Referral.Status.PENDING
            )

            # Idempotent - only complete if pending
            if referral.status == Referral.Status.COMPLETED:
                logger.info(f"Referral {referral.id} already completed")
                return referral

            # Mark as completed
            referral.mark_completed()
            logger.info(f"Referral completed on purchase: {referral.id}")

            # Check for circular referral - if circular, skip reward creation entirely
            if cls._is_circular_referral(referral):
                logger.warning(
                    f"Circular referral detected: {referral.referrer.id} <-> {user.id}. "
                    f"Skipping reward creation."
                )
                # No reward created, no extra state - referral simply completes without reward
                return referral

            # Create reward (will be pending with delayed unlock)
            if purchase_amount_cents > 0:
                ReferralRewardService.create_reward_on_referral_completion(
                    referral=referral,
                    purchase_amount_cents=purchase_amount_cents,
                    currency=currency,
                    triggering_subscription=triggering_subscription
                )

            referral = Referral.objects.select_for_update().get(pk=referral.pk)
            settings_obj = ReferralSettings.get_settings()
            if (
                purchase_amount_cents > 0
                and settings_obj.referee_benefit_enabled
                and settings_obj.referee_bonus_cents > 0
                and not referral.referee_reward_applied
            ):
                SubscriptionCreditService.add_credits(
                    user=referral.referred_user,
                    amount_cents=settings_obj.referee_bonus_cents,
                    reason="referral_bonus",
                )
                referral.referee_reward_applied = True
                referral.save(update_fields=["referee_reward_applied"])

            return referral

        except Referral.DoesNotExist:
            logger.debug(f"No pending referral found for user {user.id}")
            return None
        except Exception as e:
            logger.error(f"Error completing referral for user {user.id}: {e}")
            return None

    @classmethod
    def get_referral_stats(cls, user: User) -> dict:
        """Get referral statistics for a user including reward data."""
        referrals = Referral.objects.filter(referrer=user)
        rewards = ReferralReward.objects.filter(referrer=user)
        balance = UserRewardBalance(user)

        return {
            "total_referrals": referrals.count(),
            "completed": referrals.filter(status=Referral.Status.COMPLETED).count(),
            "pending": referrals.filter(status=Referral.Status.PENDING).count(),
            "referral_code": getattr(user, "referral_code", None),
            "total_rewards_earned_cents": sum(r.amount_cents for r in rewards if r.status != ReferralReward.Status.EXPIRED),
            "total_rewards_available_cents": balance.total_cents,
            "reward_buckets": balance.reward_count,
            "pending_rewards_cents": sum(
                r.amount_cents for r in rewards 
                if r.status == ReferralReward.Status.PENDING
            ),
        }

    @classmethod
    def get_checkout_discount(cls, user: User, amount_cents: int) -> dict:
        """
        Returns discount info for checkout.
        Safe to call outside transactions (read-only).

        Returns:
            {
                'has_discount': bool,
                'discount_percent': int,
                'final_amount_cents': int,
                'referral': Referral or None,
                'reason': str or None
            }
        """
        from apps.subscriptions.models import Subscription

        # User already has an active subscription → no discount
        if Subscription.objects.filter(user=user, is_active=True).exists():
            return {
                'has_discount': False,
                'reason': 'existing_subscription',
                'final_amount_cents': amount_cents,
                'referral': None,
                'discount_percent': 0
            }

        # Find eligible referral
        referral = Referral.objects.filter(
            referred_user=user,
            discount_used=False,
            status=Referral.Status.PENDING
        ).first()

        # If no eligible referral → no discount
        if not referral:
            return {
                'has_discount': False,
                'reason': 'no_eligible_referral',
                'final_amount_cents': amount_cents,
                'referral': None,
                'discount_percent': 0
            }

        settings_obj = ReferralSettings.get_settings()
        discount_percent = getattr(settings_obj, 'referee_discount_percent', 20)
        discount_amount = int(amount_cents * discount_percent / 100)
        final_amount = amount_cents - discount_amount

        return {
            'has_discount': True,
            'discount_percent': discount_percent,
            'final_amount_cents': final_amount,
            'referral': referral,
            'reason': None
        }
