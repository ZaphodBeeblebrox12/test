"""Rewards domain — split from apps/growth/services.py (mechanical, no behavior change)."""
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
    ReferralSettings,
    ReferralReward,
    ReferralRewardLedger,
)

class UserRewardBalance:
    """
    Central helper for querying a user's total reward balance.

    Design Decision:
    We use separate ReferralReward buckets (not a central UserRewardBalance model)
    to maintain audit trails and enable partial consumption.

    This helper provides easy access to aggregated reward data.

    Usage:
        balance = UserRewardBalance(user)
        print(balance.total_cents)  # 250 (cents)
        print(balance.total_display)  # "$2.50"
        print(balance.reward_count)  # 3 (buckets)
        rewards = balance.get_consumable_rewards()  # List of ReferralReward
    """

    def __init__(self, user: User):
        self.user = user
        self._cache = None

    def _get_rewards(self) -> List[ReferralReward]:
        """Get all non-expired, credited rewards for user."""
        if self._cache is None:
            self._cache = list(ReferralReward.objects.filter(
                referrer=self.user,
                status=ReferralReward.Status.CREDITED  # Only count credited (not pending)
            ))
        return self._cache

    @property
    def total_cents(self) -> int:
        """Total available balance in cents (sum of all credited reward buckets)."""
        return sum(r.available_amount_cents for r in self._get_rewards() if not r.is_expired)

    @property
    def total_display(self) -> str:
        """Formatted balance string."""
        return f"${self.total_cents / 100:.2f}"

    @property
    def reward_count(self) -> int:
        """Number of reward buckets with available balance."""
        return len([r for r in self._get_rewards() if r.available_amount_cents > 0])

    def get_consumable_rewards(self) -> List[ReferralReward]:
        """Get rewards ordered by creation (FIFO for consumption)."""
        return [
            r for r in self._get_rewards()
            if r.available_amount_cents > 0 and not r.is_expired
        ]

    def calculate_extension_days(self, plan_price_cents: int, plan_duration_days: int = 30) -> int:
        """Calculate extension days from current balance."""
        return ReferralRewardService.calculate_pro_rata_extension_days(
            self.total_cents, plan_price_cents, plan_duration_days
        )


# ============================================================================
# REFERRAL REWARD SERVICE
# ============================================================================

class ReferralRewardService:
    """
    Service for managing referral rewards.

    Key Design Decisions:
    1. Each reward = separate bucket (ReferralReward record)
    2. User balance = sum of available amounts (via UserRewardBalance helper)
    3. Consumption = FIFO across buckets
    4. Application = explicit call via SubscriptionCreditService
    5. Rewards start as PENDING, unlock after delay (default 72 hours)
    6. Refunds block reward unlock via explicit subscription link
    """

    DECIMAL_PRECISION = Decimal("0.01")

    @classmethod
    def get_user_balance(cls, user: User) -> UserRewardBalance:
        """Get balance helper for user."""
        return UserRewardBalance(user)

    @classmethod
    def calculate_reward_amount(cls, purchase_amount_cents: int, percentage: Decimal) -> int:
        """
        Calculate reward from purchase amount and percentage.

        Formula: reward = (purchase × percentage) / 100
        Rounds to nearest cent using ROUND_HALF_UP.
        """
        purchase = Decimal(purchase_amount_cents)
        reward = (purchase * percentage) / Decimal("100")
        return int(reward.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @classmethod
    @transaction.atomic
    def create_reward_on_referral_completion(
        cls,
        referral: Referral,
        purchase_amount_cents: int,
        currency: str = "USD",
        triggering_subscription=None
    ) -> Optional[ReferralReward]:
        """
        Create a reward when a referral is completed.
        Reward starts as PENDING and unlocks after configured delay.

        Called by: Referral completion flow (after successful payment)

        Args:
            referral: The completed Referral instance
            purchase_amount_cents: Amount of the referred user's purchase
            currency: Currency of the purchase
            triggering_subscription: The subscription that triggered this reward

        Returns:
            ReferralReward if created, None if skipped (rewards disabled, already exists, etc.)
        """
        # Idempotency — one ReferralReward per referral (referrer only)
        existing = ReferralReward.objects.filter(referral=referral).first()
        if existing is not None:
            logger.info(f"Reward already exists for referral {referral.id}")
            return existing

        settings_obj = ReferralSettings.get_settings()

        # Check if rewards are enabled
        if not settings_obj.rewards_enabled:
            logger.info(f"Referral rewards disabled, skipping reward for referral {referral.id}")
            return None

        # Check minimum purchase amount
        if purchase_amount_cents < settings_obj.minimum_purchase_amount_cents:
            logger.info(
                f"Purchase amount {purchase_amount_cents} below minimum "
                f"{settings_obj.minimum_purchase_amount_cents}, skipping reward"
            )
            return None

        # Calculate reward amount
        reward_amount_cents = cls.calculate_reward_amount(
            purchase_amount_cents,
            settings_obj.default_reward_percentage
        )

        if reward_amount_cents <= 0:
            logger.info(f"Calculated reward is zero, skipping")
            return None

        # Calculate unlock time (default 72 hours from now)
        delay_hours = settings_obj.reward_delay_hours or 72
        unlocked_at = timezone.now() + timezone.timedelta(hours=delay_hours)

        # Create the reward as PENDING (not immediately credited)
        reward = ReferralReward.objects.create(
            referral=referral,
            referrer=referral.referrer,
            amount_cents=reward_amount_cents,
            currency=currency,
            referred_purchase_amount_cents=purchase_amount_cents,
            reward_percentage=settings_obj.default_reward_percentage,
            status=ReferralReward.Status.PENDING,
            unlocked_at=unlocked_at,
            triggering_subscription=triggering_subscription  # Explicit link for refund checking
        )

        logger.info(
            f"Created pending reward {reward.id}: {reward_amount_cents/100:.2f} {currency} "
            f"for referrer {referral.referrer.id}, unlocks at {unlocked_at}, "
            f"triggered by subscription {triggering_subscription.id if triggering_subscription else 'N/A'}"
        )

        return reward

    @classmethod
    @transaction.atomic
    def unlock_eligible_rewards(cls) -> int:
        """
        Process rewards that are ready to unlock (after delay).

        For each eligible reward:
        1. Check if subscription was refunded using explicit subscription link
        2. If refunded → mark as EXPIRED
        3. If not refunded → credit wallet, create ledger entry, mark CREDITED

        Returns:
            Number of rewards processed
        """
        from apps.subscriptions.models import Subscription
        from apps.payments.models import PaymentIntent

        # Find rewards that are pending and past their unlock time
        pending_rewards = ReferralReward.objects.filter(
            status=ReferralReward.Status.PENDING,
            unlocked_at__lte=timezone.now()
        ).select_related('referral', 'referral__referred_user', 'triggering_subscription')

        processed_count = 0

        for reward in pending_rewards:
            try:
                # Get the triggering subscription (explicit link)
                triggering_sub = reward.triggering_subscription

                if not triggering_sub:
                    # No subscription link - cannot verify refund status safely
                    # Skip this reward for now (will retry later)
                    logger.warning(
                        f"Reward {reward.id} has no triggering_subscription link, "
                        f"skipping unlock until link is established"
                    )
                    continue

                # Check if the subscription is still valid (not refunded/cancelled)
                # A refunded subscription would typically be marked as cancelled or have a refund record
                is_subscription_valid = (
                    triggering_sub.is_active or 
                    (triggering_sub.status == Subscription.Status.EXPIRED and 
                     triggering_sub.expires_at and 
                     triggering_sub.expires_at > timezone.now() - timezone.timedelta(days=30))
                )

                # Additional check: look for refund in PaymentIntent
                # This requires the payment to be linked to the subscription
                payment_refunded = False
                try:
                    # Try to find a refunded payment for this user/plan combo
                    # around the time of subscription creation
                    payment_refunded = PaymentIntent.objects.filter(
                        user=reward.referral.referred_user,
                        plan=triggering_sub.plan,
                        status__in=['refunded', 'canceled']  # Adjust based on your PaymentIntent statuses
                    ).exists()
                except Exception:
                    # If we can't check, default to assuming not refunded
                    # (safer to delay than to wrongly expire)
                    pass

                if payment_refunded or not is_subscription_valid:
                    # Subscription was refunded or cancelled - expire the reward
                    reward.mark_expired(reason="refunded")

                    # Create ledger entry for the expiration
                    ReferralRewardLedger.objects.create(
                        reward=reward,
                        transaction_type=ReferralRewardLedger.TransactionType.EXPIRED,
                        amount_cents=0,
                        balance_after_cents=0,
                        description=f"Reward expired: referred subscription was refunded or cancelled"
                    )

                    logger.info(
                        f"Reward {reward.id} expired: triggering subscription "
                        f"{triggering_sub.id} was refunded or cancelled"
                    )
                    processed_count += 1
                    continue

                # Subscription is still valid - credit the reward (guarded: only
                # the caller that actually flips PENDING->CREDITED creates side effects).
                if reward.mark_credited():
                    ReferralRewardLedger.objects.create(
                        reward=reward,
                        transaction_type=ReferralRewardLedger.TransactionType.CREDIT,
                        amount_cents=reward.amount_cents,
                        balance_after_cents=reward.amount_cents,
                        description=f"Referral reward credited from {reward.referral.referred_user.username}'s purchase"
                    )
                    logger.info(
                        f"Reward {reward.id} credited: {reward.amount_cents/100:.2f} "
                        f"for referrer {reward.referrer.id}"
                    )
                    processed_count += 1
                else:
                    logger.info(f"Reward {reward.id} already credited; skipping side effects")

            except Exception as e:
                logger.error(f"Error unlocking reward {reward.id}: {e}")
                continue

        return processed_count

    @classmethod
    def get_user_reward_balance(cls, user: User) -> int:
        """Legacy method - use UserRewardBalance instead for new code."""
        return UserRewardBalance(user).total_cents

    @classmethod
    def get_user_rewards(cls, user: User) -> List[ReferralReward]:
        """Get all rewards for a user."""
        return list(ReferralReward.objects.filter(referrer=user).order_by("-created_at"))

    @classmethod
    def calculate_pro_rata_extension_days(
        cls,
        reward_amount_cents: int,
        plan_price_cents: int,
        plan_duration_days: int = 30
    ) -> int:
        """
        Calculate extra subscription days from reward credit using pro-rata.

        Formula:
            extra_days = (reward_amount / plan_price) × plan_duration_days

        Example:
            - Plan costs $10 for 30 days
            - User has $2 credit
            - Extra days = (2 / 10) × 30 = 6 days

        Note: The < 1 day check is handled at consumption time to preserve user credits

        Args:
            reward_amount_cents: Available reward amount in cents
            plan_price_cents: Plan price in cents
            plan_duration_days: Duration of plan in days (from plan model)

        Returns:
            Number of extra days to extend (0 if < 1 day)
        """
        if plan_price_cents <= 0 or reward_amount_cents <= 0:
            return 0

        reward = Decimal(reward_amount_cents)
        price = Decimal(plan_price_cents)
        duration = Decimal(plan_duration_days)

        # Calculate: (reward / price) * duration
        extra_days = (reward / price) * duration

        # Round to nearest whole day using ROUND_HALF_UP
        days = int(extra_days.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        # Note: The < 1 day safeguard is handled at consumption time
        # in apply_credit_to_subscription() to preserve user credits
        return days

    @classmethod
    def estimate_extension_for_balance(
        cls,
        user: User,
        plan_price_cents: int,
        plan_duration_days: int = 30
    ) -> dict:
        """
        Estimate subscription extension for user's current reward balance.

        Returns dict with:
            - balance_cents: Current reward balance
            - balance_display: Formatted balance
            - plan_price_cents: Plan price used
            - plan_duration_days: Plan duration used
            - extra_days: Calculated extra days
            - extension_percentage: How much of plan duration this represents
            - can_extend: Whether extension is possible
            - reward_buckets: Number of reward buckets
        """
        balance = UserRewardBalance(user)
        extra_days = balance.calculate_extension_days(plan_price_cents, plan_duration_days)

        extension_percentage = 0
        if plan_duration_days > 0:
            extension_percentage = (extra_days / plan_duration_days) * 100

        return {
            "balance_cents": balance.total_cents,
            "balance_display": balance.total_display,
            "plan_price_cents": plan_price_cents,
            "plan_price_display": f"${plan_price_cents / 100:.2f}",
            "plan_duration_days": plan_duration_days,
            "extra_days": extra_days,
            "extension_percentage": round(extension_percentage, 1),
            "can_extend": extra_days > 0,
            "reward_buckets": balance.reward_count,
        }


# ============================================================================
# CENTRAL CREDIT APPLICATION SERVICE
# ============================================================================

class SubscriptionCreditService:
    """
    CENTRAL service for applying referral credit to subscriptions.

    THIS IS THE SINGLE PLACE where reward credit gets converted into subscription time.
    All credit applications should go through here.

    Usage:
        result = SubscriptionCreditService.apply_credit_to_subscription(
            user=request.user,
            subscription=new_subscription,
            plan_price_cents=plan_price_cents,
            plan_duration_days=plan_duration_days
        )

        if result:
            print(f"Extended by {result['extra_days']} days")

    Design:
        - Gets user's available credit via UserRewardBalance
        - Calculates pro-rata extension
        - Consumes rewards (FIFO - oldest first)
        - Creates audit trail in ledger
        - Extends subscription expires_at
    """

    @classmethod
    @transaction.atomic
    def apply_credit_to_subscription(
        cls,
        user: User,
        subscription,
        plan_price_cents: int,
        plan_duration_days: int = 30
    ) -> Optional[dict]:
        """
        Apply user's referral credit to extend a subscription.

        This is THE CENTRAL FUNCTION for credit → subscription conversion.

        Process:
            1. Check user's available reward balance (only CREDITED rewards)
            2. Calculate pro-rata extension days
            3. If extension > 0 days:
                a. Consume rewards (FIFO - oldest first)
                b. Create ledger entries for each consumption
                c. Extend subscription expires_at
                d. Return extension details

        Args:
            user: User whose credit to apply
            subscription: Subscription to extend
            plan_price_cents: Price of the plan (for pro-rata calc)
            plan_duration_days: Duration of plan in days

        Returns:
            Dict with extension details if applied, None if no credit available:
            {
                "extended": True,
                "extra_days": 6,
                "old_expires": datetime,
                "new_expires": datetime,
                "consumed_amount_cents": 200,
                "consumed_rewards": [
                    {"reward_id": "uuid", "amount_consumed_cents": 100},
                    ...
                ]
            }
        """
        # Get user's balance (only CREDITED rewards)
        balance = UserRewardBalance(user)
        total_credit = balance.total_cents

        if total_credit <= 0:
            logger.debug(f"No credit available for user {user.id}")
            return None

        # Calculate extension
        extra_days = ReferralRewardService.calculate_pro_rata_extension_days(
            total_credit,
            plan_price_cents,
            plan_duration_days
        )

        # SAFEGUARD: Don't consume credits if result is < 1 day
        # This preserves user credits instead of making them disappear
        if extra_days < 1:
            logger.debug(
                f"Credit {total_credit}cents would extend < 1 day ({extra_days}), "
                f"preserving credits in wallet"
            )
            return None

        # Consume rewards (FIFO - oldest first)
        consumable_rewards = balance.get_consumable_rewards()
        consumable_rewards.sort(key=lambda r: r.created_at)  # Ensure FIFO

        amount_to_consume = total_credit
        consumed_rewards = []

        for reward in consumable_rewards:
            if amount_to_consume <= 0:
                break

            available = reward.available_amount_cents
            if available <= 0:
                continue

            consume = min(available, amount_to_consume)
            reward.mark_used(consume)
            amount_to_consume -= consume

            # Create ledger entry for this consumption
            ReferralRewardLedger.objects.create(
                reward=reward,
                transaction_type=ReferralRewardLedger.TransactionType.DEBIT,
                amount_cents=-consume,
                balance_after_cents=reward.available_amount_cents,
                description=f"Applied to subscription extension ({extra_days} extra days)",
                subscription=subscription,
                metadata={
                    "extra_days": extra_days,
                    "plan_price_cents": plan_price_cents,
                }
            )

            consumed_rewards.append({
                "reward_id": str(reward.id),
                "amount_consumed_cents": consume,
            })

        # Extend the subscription
        old_expires = subscription.expires_at
        new_expires = old_expires + timedelta(days=extra_days)
        subscription.expires_at = new_expires
        subscription.save(update_fields=["expires_at"])

        actual_consumed = total_credit - amount_to_consume

        # Log credit application
        logger.info(
            f"Applied ${actual_consumed/100:.2f} credit to subscription {subscription.id} "
            f"for user {user.id}, extended by {extra_days} days"
        )

        return {
            "extended": True,
            "extra_days": extra_days,
            "old_expires": old_expires,
            "new_expires": new_expires,
            "consumed_amount_cents": actual_consumed,
            "consumed_rewards": consumed_rewards,
        }

    @classmethod
    def calculate_potential_extension(
        cls,
        user: User,
        plan_price_cents: int,
        plan_duration_days: int = 30
    ) -> dict:
        """
        Calculate what extension would look like without applying it.
        Useful for showing users "what you'll get" before purchase.
        """
        return ReferralRewardService.estimate_extension_for_balance(
            user, plan_price_cents, plan_duration_days
        )

    @classmethod
    def add_credits(cls, user: User, amount_cents: int, reason: str = "") -> None:
        """
        Apply referee / non-referrer credits. Does NOT use ReferralReward (referrer-only).

        Hook point: extend this to post to your wallet, promo ledger, or billing credits.
        """
        if amount_cents <= 0:
            return
        logger.info(
            "SubscriptionCreditService.add_credits: user_id=%s amount_cents=%s reason=%s",
            user.pk,
            amount_cents,
            reason or "",
        )
