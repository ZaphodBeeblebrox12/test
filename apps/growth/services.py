"""
Growth services for gift invites, referral tracking, and referral rewards.

Architecture Notes:
- Each reward = separate bucket (ReferralReward record)
- User balance = sum of available amounts (via UserRewardBalance helper)
- Consumption = FIFO across buckets
- Application = explicit call via SubscriptionCreditService
"""
import logging
from typing import Optional, Tuple, List
from datetime import timedelta
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

from .models import (
    GiftInvite, 
    PendingGiftClaim, 
    Referral, 
    ReferralCode,
    ReferralSettings,
    ReferralReward,
    ReferralRewardLedger,
)

logger = logging.getLogger(__name__)


# ============================================================================
# USER REWARD BALANCE - Central Query Helper
# ============================================================================

class UserRewardBalance:
    """
    Central helper for querying a user's total reward balance.

    Design Decision:
    We use separate ReferralReward buckets (not a central UserRewardBalance model)
    to maintain audit trails and enable partial consumption.

    This helper provides easy access to aggregated reward data.

    Usage:
        balance = UserRewardBalance(user)
        print(balance.total_cents)        # 250 (cents)
        print(balance.total_display)      # "$2.50"
        print(balance.reward_count)       # 3 (buckets)
        rewards = balance.get_consumable_rewards()  # List of ReferralReward
    """

    def __init__(self, user: User):
        self.user = user
        self._cache = None

    def _get_rewards(self) -> List[ReferralReward]:
        """Get all non-expired rewards for user."""
        if self._cache is None:
            self._cache = list(ReferralReward.objects.filter(
                referrer=self.user,
                status__in=[ReferralReward.Status.CREDITED, ReferralReward.Status.PENDING]
            ))
        return self._cache

    @property
    def total_cents(self) -> int:
        """Total available balance in cents (sum of all reward buckets)."""
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
        currency: str = "USD"
    ) -> Optional[ReferralReward]:
        """
        Create a reward when a referral is completed.

        Called by: Referral completion flow (after successful payment)

        Args:
            referral: The completed Referral instance
            purchase_amount_cents: Amount of the referred user's purchase
            currency: Currency of the purchase

        Returns:
            ReferralReward if created, None if skipped (rewards disabled, already exists, etc.)
        """
        # Idempotency check - don't create duplicate rewards
        if hasattr(referral, 'reward') and referral.reward is not None:
            logger.info(f"Reward already exists for referral {referral.id}")
            return referral.reward

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

        # Create the reward + ledger entry atomically
        reward = ReferralReward.objects.create(
            referral=referral,
            referrer=referral.referrer,
            amount_cents=reward_amount_cents,
            currency=currency,
            referred_purchase_amount_cents=purchase_amount_cents,
            reward_percentage=settings_obj.default_reward_percentage,
            status=ReferralReward.Status.CREDITED
        )

        ReferralRewardLedger.objects.create(
            reward=reward,
            transaction_type=ReferralRewardLedger.TransactionType.CREDIT,
            amount_cents=reward_amount_cents,
            balance_after_cents=reward_amount_cents,
            description=f"Referral reward earned from {referral.referred_user.username}'s purchase"
        )

        logger.info(
            f"Created reward {reward.id}: {reward_amount_cents/100:.2f} {currency} "
            f"for referrer {referral.referrer.id}"
        )

        return reward

    @classmethod
    def get_user_reward_balance(cls, user: User) -> int:
        """Legacy method - use UserRewardBalance instead for new code."""
        return UserRewardBalance(user).total_cents

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

        Args:
            reward_amount_cents: Available reward amount in cents
            plan_price_cents: Plan price in cents
            plan_duration_days: Duration of plan in days (default 30)

        Returns:
            Number of extra days to extend
        """
        if plan_price_cents <= 0 or reward_amount_cents <= 0:
            return 0

        reward = Decimal(reward_amount_cents)
        price = Decimal(plan_price_cents)
        duration = Decimal(plan_duration_days)

        # Calculate: (reward / price) * duration
        extra_days = (reward / price) * duration

        # Round to nearest whole day using ROUND_HALF_UP
        return int(extra_days.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

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
            plan_duration_days=30
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
        1. Check user's available reward balance
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
        # Get user's balance
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

        if extra_days <= 0:
            logger.debug(f"Credit {total_credit}cents too small for extension")
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

            consumed_rewards.append({
                "reward_id": str(reward.id),
                "amount_consumed_cents": consume,
            })

            # Create ledger entry
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

        # Extend the subscription
        old_expires = subscription.expires_at
        new_expires = old_expires + timedelta(days=extra_days)
        subscription.expires_at = new_expires
        subscription.save(update_fields=["expires_at"])

        actual_consumed = total_credit - amount_to_consume

        logger.info(
            f"Extended subscription {subscription.id} by {extra_days} days "
            f"using ${actual_consumed/100:.2f} credit for user {user.id}"
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


# ============================================================================
# REFERRAL SERVICE (Purchase Completion)
# ============================================================================

class ReferralService:
    """
    Service for handling referral tracking and reward creation.

    TRIGGER LOCATION NOTE:
    Currently, complete_referral_on_purchase is called from payments/views.py
    after successful payment. This works but couples referral rewards to the
    payment view.

    For future-proofing, consider moving to:
    - Signal on Subscription post_save (when status becomes ACTIVE)
    - Payment provider webhook handler
    - Event bus / outbox pattern (for high scale)

    The important invariant: reward is ONLY created after confirmed payment.
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
    @transaction.atomic
    def record_referral_signup(cls, referred_user: User, code: str) -> Optional[Referral]:
        """
        Record that a user signed up with a referral code.
        Called during signup flow. Referral stays PENDING.
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

        # Check if referred_user already has a referral record
        if hasattr(referred_user, "referred_by"):
            logger.info(f"User {referred_user.id} already has referral record")
            return referred_user.referred_by

        # Create referral record (starts as pending - will complete on purchase)
        referral = Referral.objects.create(
            referrer=referrer,
            referred_user=referred_user,
            status=Referral.Status.PENDING
        )

        logger.info(f"Referral recorded (pending): {referrer.id} -> {referred_user.id}")
        return referral

    @classmethod
    @transaction.atomic
    def complete_referral_on_purchase(
        cls,
        user: User,
        purchase_amount_cents: int = 0,
        currency: str = "USD"
    ) -> Optional[Referral]:
        """
        Mark a user's referral as completed after successful purchase.
        Also creates reward if applicable.

        Args:
            user: User who just made a purchase
            purchase_amount_cents: Amount of the purchase (for reward calculation)
            currency: Currency of the purchase

        Returns:
            Referral object if completed, None if no pending referral or already completed
        """
        try:
            referral = Referral.objects.select_for_update().get(
                referred_user=user,
                status=Referral.Status.PENDING
            )

            # Idempotent - only complete if pending
            if referral.status == Referral.Status.PENDING:
                referral.mark_completed()
                logger.info(f"Referral completed on purchase: {referral.id}")

                # Create reward if purchase amount provided
                if purchase_amount_cents > 0:
                    ReferralRewardService.create_reward_on_referral_completion(
                        referral=referral,
                        purchase_amount_cents=purchase_amount_cents,
                        currency=currency
                    )

                return referral

        except Referral.DoesNotExist:
            logger.debug(f"No pending referral found for user {user.id}")
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
            "total_rewards_earned_cents": sum(r.amount_cents for r in rewards),
            "total_rewards_available_cents": balance.total_cents,
            "reward_buckets": balance.reward_count,
        }


# ============================================================================
# GIFT SERVICES (Existing - preserved)
# ============================================================================

class GiftServiceError(Exception):
    """Base exception for gift service errors."""
    pass


class GiftAlreadyClaimedError(GiftServiceError):
    """Raised when attempting to claim an already claimed gift."""
    pass


class GiftExpiredError(GiftServiceError):
    """Raised when attempting to claim an expired gift."""
    pass


class GiftEmailMismatchError(GiftServiceError):
    """Raised when claimer's email doesn't match gift recipient."""
    pass


class SelfGiftError(GiftServiceError):
    """Raised when user tries to gift themselves."""
    pass


class InvalidGiftCodeError(GiftServiceError):
    """Raised when legacy gift code is invalid."""
    pass


class AttributionRequiredError(GiftServiceError):
    """Raised when attribution is missing (should never happen)."""
    pass


class GiftService:
    """Service for creating and managing gift invites."""

    DEFAULT_INVITE_EXPIRY_DAYS = 30
    DEFAULT_GIFT_DURATION_DAYS = 30

    @classmethod
    @transaction.atomic
    def create_gift(
        cls,
        from_user: User,
        recipient_email: str,
        plan,
        duration_days: int = None,
        message: str = "",
        request=None,
    ) -> Tuple[object, GiftInvite]:
        """Create a complete gift with both GiftSubscription and GiftInvite."""
        recipient_email = recipient_email.lower().strip()

        if from_user.email and from_user.email.lower() == recipient_email:
            raise SelfGiftError("Cannot gift to your own email address")

        duration_days = duration_days or cls.DEFAULT_GIFT_DURATION_DAYS

        gift_sub = api_create_gift_subscription(
            from_user=from_user,
            plan=plan,
            duration_days=duration_days,
            message=message,
            request=request,
        )

        claim_token = GiftInvite.generate_token()
        token_hash = GiftInvite.hash_token(claim_token)
        expires_at = timezone.now() + timedelta(days=cls.DEFAULT_INVITE_EXPIRY_DAYS)

        gift_invite = GiftInvite.objects.create(
            gift_subscription=gift_sub,
            recipient_email=recipient_email,
            claim_token=claim_token,
            claim_token_hash=token_hash,
            expires_at=expires_at,
            status=GiftInvite.Status.PENDING,
        )

        logger.info(
            f"Created gift invite {gift_invite.id} for {recipient_email} "
            f"from user {from_user.id}"
        )

        return gift_sub, gift_invite

    @classmethod
    def get_gift_by_token(cls, token: str) -> Optional[GiftInvite]:
        """Look up a gift invite by its claim token."""
        token_hash = GiftInvite.hash_token(token)
        try:
            return GiftInvite.objects.select_related(
                'gift_subscription',
                'gift_subscription__plan',
                'gift_subscription__from_user',
            ).get(claim_token_hash=token_hash)
        except GiftInvite.DoesNotExist:
            return None

    @classmethod
    def can_resend_email(cls, gift_invite: GiftInvite) -> bool:
        """Check if email can be resent for this invite."""
        return gift_invite.can_resend_email

    @classmethod
    @transaction.atomic
    def record_email_sent(cls, gift_invite: GiftInvite) -> None:
        """Record that the gift email was sent."""
        now = timezone.now()
        if gift_invite.email_sent_at is None:
            gift_invite.email_sent_at = now
        gift_invite.last_email_sent_at = now
        gift_invite.email_resend_count += 1
        gift_invite.save(update_fields=[
            'email_sent_at',
            'last_email_sent_at',
            'email_resend_count'
        ])


class LegacyGiftService:
    """Service for handling legacy gift_code claims."""

    @classmethod
    def _build_attribution(cls, gift) -> GiftAttribution:
        """Build mandatory attribution for a legacy gift."""
        if not gift.from_user:
            raise AttributionRequiredError("Gift has no sender - cannot build attribution")
        return GiftAttribution(
            source="gift",
            gift_id=str(gift.id),
            claimed_from_email=gift.from_user.email or gift.from_user.username,
        )

    @classmethod
    def validate_legacy_claim(cls, gift, user: User) -> None:
        """Validate that a legacy gift can be claimed."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        if gift.status == LegacyGiftSubscription.Status.CLAIMED:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift.to_user is not None:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift.expires_at and gift.expires_at < timezone.now():
            raise GiftExpiredError("This gift has expired")
        if gift.status == LegacyGiftSubscription.Status.EXPIRED:
            raise GiftExpiredError("This gift has expired")
        if gift.from_user_id == user.id:
            raise SelfGiftError("Cannot claim your own gift")

    @classmethod
    @transaction.atomic
    def claim_legacy_gift(cls, gift_code: str, user: User, request=None) -> Tuple[object, object]:
        """Claim a legacy gift using the gift code."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        gift_code = gift_code.upper().strip()
        gift = get_gift_by_code(gift_code)

        if not gift:
            raise InvalidGiftCodeError("Invalid gift code")

        try:
            gift = LegacyGiftSubscription.objects.select_related(
                'plan', 'from_user'
            ).select_for_update(nowait=False).get(
                id=gift.id,
                status=LegacyGiftSubscription.Status.PENDING
            )
        except LegacyGiftSubscription.DoesNotExist:
            raise InvalidGiftCodeError("Gift no longer available")

        cls.validate_legacy_claim(gift, user)
        attribution = cls._build_attribution(gift)

        existing_sub = get_active_subscription(user)

        if existing_sub:
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=gift.plan,
                duration_days=gift.duration_days,
                attribution=attribution,
                request=request,
            )
        else:
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=gift.plan,
                duration_days=gift.duration_days,
                attribution=attribution,
                request=request,
            )

        gift.to_user = user
        gift.status = LegacyGiftSubscription.Status.CLAIMED
        gift.claimed_at = timezone.now()
        gift.resulting_subscription = subscription
        gift.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        return gift, subscription


class GiftClaimService:
    """Service for handling token-based gift claims."""

    @classmethod
    def _build_attribution(cls, gift_sub) -> GiftAttribution:
        """Build mandatory attribution for a token-based gift."""
        if not gift_sub.from_user:
            raise AttributionRequiredError("Gift has no sender - cannot build attribution")
        return GiftAttribution(
            source="gift",
            gift_id=str(gift_sub.id),
            claimed_from_email=gift_sub.from_user.email or gift_sub.from_user.username,
        )

    @classmethod
    def validate_claim(cls, gift_invite: GiftInvite, user: User) -> None:
        """Validate that a gift can be claimed."""
        if gift_invite.status == GiftInvite.Status.CLAIMED:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift_invite.claimed_by is not None:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift_invite.is_expired:
            raise GiftExpiredError("This gift has expired")
        if gift_invite.status == GiftInvite.Status.EXPIRED:
            raise GiftExpiredError("This gift has expired")

        user_email = user.email.lower().strip() if user.email else None
        recipient_email = gift_invite.recipient_email.lower().strip()

        if user_email != recipient_email:
            raise GiftEmailMismatchError(
                f"This gift was sent to {recipient_email}. "
                f"Please log in with that email address."
            )

        if gift_invite.gift_subscription.from_user_id == user.id:
            raise SelfGiftError("Cannot claim your own gift")

    @classmethod
    @transaction.atomic
    def claim_gift(cls, token: str, user: User, request=None) -> object:
        """Claim a gift for the given user."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        token_hash = GiftInvite.hash_token(token)
        try:
            gift_invite = GiftInvite.objects.select_related(
                'gift_subscription',
                'gift_subscription__plan',
                'gift_subscription__from_user',
            ).select_for_update(nowait=False).get(claim_token_hash=token_hash)
        except GiftInvite.DoesNotExist:
            raise GiftServiceError("Invalid gift token")

        cls.validate_claim(gift_invite, user)

        gift_sub = gift_invite.gift_subscription
        plan = gift_sub.plan
        duration_days = gift_sub.duration_days

        attribution = cls._build_attribution(gift_sub)
        existing_sub = get_active_subscription(user)

        if existing_sub:
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )
        else:
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )

        gift_invite.status = GiftInvite.Status.CLAIMED
        gift_invite.claimed_by = user
        gift_invite.claimed_at = timezone.now()
        gift_invite.save(update_fields=['status', 'claimed_by', 'claimed_at'])

        gift_sub.to_user = user
        gift_sub.status = LegacyGiftSubscription.Status.CLAIMED
        gift_sub.claimed_at = timezone.now()
        gift_sub.resulting_subscription = subscription
        gift_sub.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        cls._cleanup_pending_claims(token_hash, user)
        return subscription

    @classmethod
    def _cleanup_pending_claims(cls, token_hash: str, user: User) -> None:
        """Clean up pending claims after successful claim."""
        PendingGiftClaim.objects.filter(
            claim_token_hash=token_hash,
            status=PendingGiftClaim.Status.PENDING
        ).update(
            status=PendingGiftClaim.Status.PROCESSED,
            processed_at=timezone.now(),
            processed_by=user
        )

    @classmethod
    def store_pending_claim(cls, token: str, session_key: str, request=None) -> PendingGiftClaim:
        """Store a pending claim for anonymous users."""
        token_hash = GiftInvite.hash_token(token)
        existing = PendingGiftClaim.objects.filter(
            claim_token_hash=token_hash,
            session_key=session_key,
            status=PendingGiftClaim.Status.PENDING
        ).first()

        if existing:
            return existing

        ip_address = None
        user_agent = ""
        if request:
            ip_address = cls._get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

        pending = PendingGiftClaim.objects.create(
            claim_token_hash=token_hash,
            session_key=session_key,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        logger.info(f"Stored pending claim {pending.id} for token {token_hash[:8]}...")
        return pending

    @classmethod
    def process_pending_claims_for_user(cls, user: User, session_key: str, request=None) -> Optional[object]:
        """Process any pending claims for a newly authenticated user."""
        pending = PendingGiftClaim.objects.filter(
            session_key=session_key,
            status=PendingGiftClaim.Status.PENDING
        ).select_related().first()

        if not pending:
            return None

        gift_invite = GiftInvite.objects.filter(
            claim_token_hash=pending.claim_token_hash
        ).first()

        if not gift_invite:
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = "Gift invite not found"
            pending.save(update_fields=['status', 'error_message'])
            return None

        try:
            subscription = cls._claim_by_invite(
                gift_invite=gift_invite,
                user=user,
                request=request,
            )
            pending.status = PendingGiftClaim.Status.PROCESSED
            pending.processed_at = timezone.now()
            pending.processed_by = user
            pending.save(update_fields=['status', 'processed_at', 'processed_by'])
            return subscription

        except GiftEmailMismatchError as e:
            pending.error_message = str(e)
            pending.save(update_fields=['error_message'])
            return None
        except (GiftAlreadyClaimedError, GiftExpiredError) as e:
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = str(e)
            pending.save(update_fields=['status', 'error_message'])
            return None
        except Exception as e:
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = str(e)
            pending.save(update_fields=['status', 'error_message'])
            logger.error(f"Pending claim error for user {user.id}: {e}")
            return None

    @classmethod
    @transaction.atomic
    def _claim_by_invite(cls, gift_invite: GiftInvite, user: User, request=None) -> object:
        """Claim a gift by invite object (internal method for pending claims)."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        cls.validate_claim(gift_invite, user)
        gift_invite = GiftInvite.objects.select_for_update().get(pk=gift_invite.pk)
        cls.validate_claim(gift_invite, user)

        gift_sub = gift_invite.gift_subscription
        plan = gift_sub.plan
        duration_days = gift_sub.duration_days

        attribution = cls._build_attribution(gift_sub)
        existing_sub = get_active_subscription(user)

        if existing_sub:
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )
        else:
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )

        gift_invite.status = GiftInvite.Status.CLAIMED
        gift_invite.claimed_by = user
        gift_invite.claimed_at = timezone.now()
        gift_invite.save(update_fields=['status', 'claimed_by', 'claimed_at'])

        gift_sub.to_user = user
        gift_sub.status = LegacyGiftSubscription.Status.CLAIMED
        gift_sub.claimed_at = timezone.now()
        gift_sub.resulting_subscription = subscription
        gift_sub.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        return subscription

    @staticmethod
    def _get_client_ip(request) -> Optional[str]:
        """Extract client IP from request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class GiftEmailService:
    """Service for sending gift-related emails."""

    @classmethod
    def send_gift_email(cls, gift_invite: GiftInvite, claim_url: str, resend: bool = False) -> bool:
        """Send gift invitation email."""
        from apps.notifications.services import NotificationService

        if resend and not GiftService.can_resend_email(gift_invite):
            logger.warning(f"Cannot resend email for gift {gift_invite.id}: rate limit exceeded")
            return False

        gift_sub = gift_invite.gift_subscription
        from_user = gift_sub.from_user
        plan = gift_sub.plan

        sender_display_name = cls._get_user_display_name(from_user)

        context = {
            'recipient_email': gift_invite.recipient_email,
            'sender_name': sender_display_name,
            'sender_email': from_user.email,
            'plan_name': plan.name,
            'duration_days': gift_sub.duration_days,
            'message': gift_sub.message,
            'claim_url': claim_url,
            'expires_at': gift_invite.expires_at,
        }

        try:
            NotificationService.send_email(
                to_email=gift_invite.recipient_email,
                template='growth/gift_invite',
                subject=f"You've received a gift subscription from {sender_display_name}!",
                context=context,
                metadata={
                    'gift_invite_id': str(gift_invite.id),
                    'gift_subscription_id': str(gift_sub.id),
                    'is_resend': resend,
                }
            )

            GiftService.record_email_sent(gift_invite)

            logger.info(
                f"{'Resent' if resend else 'Sent'} gift email for {gift_invite.id} "
                f"to {gift_invite.recipient_email}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send gift email for {gift_invite.id}: {e}")
            return False

    @classmethod
    def send_claim_confirmation_email(cls, user: User, subscription: object, gift_invite: GiftInvite) -> bool:
        """Send confirmation email after successful gift claim."""
        from apps.notifications.services import NotificationService

        gift_sub = gift_invite.gift_subscription
        sender_display_name = cls._get_user_display_name(gift_sub.from_user)

        context = {
            'user': user,
            'plan_name': gift_sub.plan.name,
            'duration_days': gift_sub.duration_days,
            'subscription_expires': subscription.expires_at,
            'sender_name': sender_display_name,
        }

        try:
            NotificationService.send_email(
                to_email=user.email,
                template='growth/gift_claimed',
                subject="Your gift subscription has been activated!",
                context=context,
                metadata={
                    'gift_invite_id': str(gift_invite.id),
                    'subscription_id': str(subscription.id),
                }
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send claim confirmation: {e}")
            return False

    @classmethod
    def _get_user_display_name(cls, user: User) -> str:
        """Get display name: nickname > first_name > username."""
        if hasattr(user, 'nickname') and user.nickname:
            return user.nickname
        if user.first_name:
            return user.first_name
        return user.username
