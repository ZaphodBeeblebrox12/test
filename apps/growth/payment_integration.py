"""
Payment Integration Patch for Referral Safety
Add this to your payment processing logic
"""
import logging
from django.db import transaction
from apps.growth.models import Referral
from apps.growth.validators import ReferralSafetyService, ReferralValidator, ReferralSafetyError

logger = logging.getLogger(__name__)


class PaymentReferralIntegration:
    """
    Integration point for payment processing and referral rewards
    Add this to your payment success handler
    """

    @staticmethod
    @transaction.atomic
    def process_successful_payment(user, payment_amount_cents: int, 
                                   currency: str = 'USD', payment_status: str = 'success') -> dict:
        """
        Process referral logic when payment is successful

        Args:
            user: The user who made the payment (referee)
            payment_amount_cents: Payment amount in cents
            currency: Currency code
            payment_status: Must be 'success' to trigger rewards

        Returns:
            dict: Result of referral processing
        """
        result = {
            'discount_applied': False,
            'discount_amount_cents': 0,
            'reward_created': False,
            'reward_id': None,
            'errors': []
        }

        try:
            # Find referral for this user (if they were referred)
            referral = Referral.objects.filter(
                referred_user=user,
                status='completed'
            ).select_related('referrer').first()

            if not referral:
                logger.info(f"No referral found for user {user.id}")
                return result

            # SAFETY 1: Apply discount if available (one-time)
            try:
                if not referral.discount_used:
                    discounted_amount = ReferralSafetyService.apply_referee_discount(
                        referral, payment_amount_cents
                    )
                    result['discount_applied'] = True
                    result['discount_amount_cents'] = payment_amount_cents - discounted_amount
                    logger.info(f"Discount applied for user {user.id}: {result['discount_amount_cents']}cents")
            except Exception as e:
                logger.error(f"Error applying discount: {e}")
                result['errors'].append(f"Discount error: {str(e)}")

            # SAFETY 2: Create reward only if payment successful
            try:
                reward = ReferralSafetyService.create_reward_safe(
                    referral=referral,
                    payment_status=payment_status,
                    payment_amount_cents=payment_amount_cents,
                    currency=currency
                )
                result['reward_created'] = True
                result['reward_id'] = str(reward.id)
                logger.info(f"Reward created for referral {referral.id}")

            except ReferralSafetyError as e:
                logger.warning(f"Reward creation blocked: {e}")
                result['errors'].append(str(e))

            # SAFETY 3: Check fraud indicators
            fraud_check = ReferralSafetyService.check_fraud_indicators(referral)
            if fraud_check['is_suspicious']:
                logger.warning(f"Fraud indicators detected: {fraud_check['reasons']}")
                result['fraud_flags'] = fraud_check['reasons']

        except Exception as e:
            logger.error(f"Error processing referral payment: {e}")
            result['errors'].append(f"Processing error: {str(e)}")

        return result

    @staticmethod
    def get_referee_discount_amount(user, original_amount_cents: int) -> int:
        """
        Get discounted amount for referee at checkout
        Returns discounted amount or original if no discount available
        """
        try:
            referral = Referral.objects.filter(
                referred_user=user,
                status='completed',
                discount_used=False
            ).first()

            if not referral:
                return original_amount_cents

            # Calculate 20% discount
            discount_percentage = 20
            discount_amount = int(original_amount_cents * discount_percentage / 100)

            return original_amount_cents - discount_amount

        except Exception as e:
            logger.error(f"Error calculating discount: {e}")
            return original_amount_cents


# Example usage in your payment view:
"""
from apps.growth.payment_integration import PaymentReferralIntegration

class CheckoutView(View):
    def post(self, request):
        # ... process payment ...

        if payment_successful:
            # Process referral rewards
            referral_result = PaymentReferralIntegration.process_successful_payment(
                user=request.user,
                payment_amount_cents=amount_cents,
                currency=currency,
                payment_status='success'
            )

            if referral_result['discount_applied']:
                messages.success(request, f"Referral discount applied!")

            if referral_result['reward_created']:
                messages.success(request, f"Referral reward unlocked!")

        # ... rest of view ...
"""
