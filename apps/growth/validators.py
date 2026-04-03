"""
Referral Safety Validators
Critical fraud prevention functions
"""
import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.growth.models import Referral, ReferralReward

logger = logging.getLogger(__name__)


class ReferralSafetyError(Exception):
    """Custom exception for referral safety violations"""
    pass


class ReferralValidator:
    """Validates referral operations for fraud prevention"""

    @staticmethod
    def validate_self_referral(referrer_id: int, referred_user_id: int) -> None:
        """
        ❗ CRITICAL: Block self-referral
        Raises ReferralSafetyError if user tries to refer themselves
        """
        if referrer_id == referred_user_id:
            logger.warning(f"🚫 SELF-REFERRAL BLOCKED: User {referrer_id} tried to refer themselves")
            raise ReferralSafetyError("Cannot refer yourself")

    @staticmethod
    def validate_no_existing_referral(referred_user_id: int) -> None:
        """
        ❗ CRITICAL: Prevent multiple referrals for same user
        Each user can only be referred once
        """
        existing = Referral.objects.filter(
            referred_user_id=referred_user_id,
            status__in=['pending', 'completed']
        ).first()

        if existing:
            logger.warning(f"🚫 DUPLICATE REFERRAL BLOCKED: User {referred_user_id} already referred by {existing.referrer_id}")
            raise ReferralSafetyError("This user has already been referred")

    @staticmethod
    def validate_discount_available(referral: Referral) -> bool:
        """
        ❗ CRITICAL: One-time discount enforcement
        Returns True if discount can be applied, False if already used
        """
        if referral.discount_used:
            logger.info(f"🚫 DISCOUNT ALREADY USED: Referral {referral.id}")
            return False
        return True

    @staticmethod
    def validate_reward_not_exists(referral: Referral) -> None:
        """
        ❗ CRITICAL: Single reward per referral
        Raises error if reward already created
        """
        if referral.reward_created:
            logger.warning(f"🚫 REWARD ALREADY EXISTS: Referral {referral.id}")
            raise ReferralSafetyError("Reward already created for this referral")

        # Double-check database
        existing_reward = ReferralReward.objects.filter(referral=referral).first()
        if existing_reward:
            logger.warning(f"🚫 REWARD FOUND IN DB: Referral {referral.id}, Reward {existing_reward.id}")
            raise ReferralSafetyError("Reward already exists in database")

    @staticmethod
    def validate_payment_successful(payment_status: str) -> None:
        """
        ❗ CRITICAL: Reward only after successful payment
        """
        if payment_status != 'success':
            logger.warning(f"🚫 REWARD BLOCKED: Payment status is {payment_status}, not 'success'")
            raise ReferralSafetyError(f"Cannot create reward for payment status: {payment_status}")


class ReferralSafetyService:
    """Service for safe referral operations with fraud prevention"""

    @staticmethod
    @transaction.atomic
    def apply_referee_discount(referral: Referral, payment_amount_cents: int) -> int:
        """
        Apply one-time discount for referee
        Returns discounted amount (or original if discount unavailable)
        """
        # Validate discount available
        if not ReferralValidator.validate_discount_available(referral):
            logger.info(f"ℹ️ No discount available for referral {referral.id}")
            return payment_amount_cents

        # Calculate discount (e.g., 20% off first payment)
        discount_percentage = 20  # Configurable
        discount_amount = int(payment_amount_cents * discount_percentage / 100)
        discounted_total = payment_amount_cents - discount_amount

        # Mark discount as used
        referral.discount_used = True
        referral.save(update_fields=['discount_used'])

        logger.info(f"✅ DISCOUNT APPLIED: Referral {referral.id}, Saved {discount_amount}cents")

        return discounted_total

    @staticmethod
    @transaction.atomic
    def create_reward_safe(referral: Referral, payment_status: str, 
                          payment_amount_cents: int, currency: str = 'USD') -> 'ReferralReward':
        """
        Safely create referral reward with all validations

        Args:
            referral: The referral object
            payment_status: Must be 'success'
            payment_amount_cents: Amount in cents for reward calculation
            currency: Currency code

        Returns:
            ReferralReward: Created reward

        Raises:
            ReferralSafetyError: If any validation fails
        """
        from apps.growth.models import ReferralSettings

        # ❗ VALIDATION 1: Payment must be successful
        ReferralValidator.validate_payment_successful(payment_status)

        # ❗ VALIDATION 2: No existing reward
        ReferralValidator.validate_reward_not_exists(referral)

        # ❗ VALIDATION 3: Not self-referral
        ReferralValidator.validate_self_referral(
            referral.referrer_id, 
            referral.referred_user_id
        )

        # Get reward percentage from settings
        settings = ReferralSettings.get_settings()
        reward_percentage = settings.default_reward_percentage if settings else 20

        # Calculate reward amount
        reward_amount_cents = int(payment_amount_cents * float(reward_percentage) / 100)

        # Ensure minimum reward
        if reward_amount_cents < 100:  # Minimum $1
            logger.warning(f"⚠️ Reward amount too low: {reward_amount_cents}cents, skipping")
            raise ReferralSafetyError("Reward amount below minimum threshold")

        # Create reward with delay
        from django.utils import timezone

        unlock_delay_hours = settings.reward_delay_hours if settings else 72
        unlocked_at = timezone.now() + timezone.timedelta(hours=unlock_delay_hours)

        reward = ReferralReward.objects.create(
            referral=referral,
            referrer=referral.referrer,
            amount_cents=reward_amount_cents,
            currency=currency,
            referred_purchase_amount_cents=payment_amount_cents,
            reward_percentage=reward_percentage,
            status='pending',
            unlocked_at=unlocked_at
        )

        # Mark referral as having reward created
        referral.reward_created = True
        referral.save(update_fields=['reward_created'])

        logger.info(f"✅ REWARD CREATED: ID {reward.id}, Amount {reward_amount_cents}cents, Unlocks {unlocked_at}")

        return reward

    @staticmethod
    def check_fraud_indicators(referral: Referral) -> dict:
        """
        Check for common fraud patterns
        Returns dict of fraud indicators
        """
        indicators = {
            'is_suspicious': False,
            'reasons': []
        }

        # Check 1: Same email domain pattern
        referrer_email = referral.referrer.email.split('@')[1] if referral.referrer.email else ''
        referred_email = referral.referred_user.email.split('@')[1] if referral.referred_user.email else ''

        if referrer_email == referred_email and referrer_email in ['tempmail.com', '10minutemail.com']:
            indicators['is_suspicious'] = True
            indicators['reasons'].append('Disposable email domain')

        # Check 2: Rapid signup (within 5 minutes)
        from django.utils import timezone
        time_diff = referral.referred_user.date_joined - referral.created_at
        if time_diff.total_seconds() < 300:  # 5 minutes
            indicators['is_suspicious'] = True
            indicators['reasons'].append('Rapid signup (possible automation)')

        # Check 3: Same IP (if tracked)
        # Add your IP tracking logic here

        if indicators['is_suspicious']:
            logger.warning(f"🚨 FRAUD INDICATORS: Referral {referral.id}, Reasons: {indicators['reasons']}")

        return indicators


# Convenience function for views/services
def safe_create_referral_reward(referral_id: int, payment_status: str, 
                                payment_amount_cents: int, currency: str = 'USD') -> 'ReferralReward':
    """
    Convenience function to safely create reward by referral ID
    """
    referral = Referral.objects.select_related('referrer', 'referred_user').get(id=referral_id)
    return ReferralSafetyService.create_reward_safe(
        referral, payment_status, payment_amount_cents, currency
    )
