"""
Signup Safety Patch
Prevents self-referral and duplicate referrals during signup
"""
import logging
from django.db import transaction
from apps.growth.models import Referral, ReferralCode
from apps.growth.validators import ReferralValidator, ReferralSafetyError

logger = logging.getLogger(__name__)


class SignupReferralSafety:
    """
    Safety checks during user signup with referral code
    """

    @staticmethod
    def validate_referral_code(referral_code: str, new_user) -> dict:
        """
        Validate referral code during signup

        Returns:
            {
                'valid': bool,
                'referral_code_obj': ReferralCode or None,
                'error': str or None
            }
        """
        result = {
            'valid': False,
            'referral_code_obj': None,
            'error': None
        }

        if not referral_code:
            return result

        try:
            # Find the referral code
            code_obj = ReferralCode.objects.select_related('user').get(
                code=referral_code.upper().strip()
            )

            # ❗ SAFETY: Check self-referral
            if code_obj.user.id == new_user.id:
                result['error'] = "Cannot use your own referral code"
                logger.warning(f"SELF-REFERRAL BLOCKED: User {new_user.id} tried own code")
                return result

            # ❗ SAFETY: Check if user already has a referral
            existing = Referral.objects.filter(
                referred_user=new_user,
                status__in=['pending', 'completed']
            ).first()

            if existing:
                result['error'] = "You have already been referred by another user"
                logger.warning(f"DUPLICATE REFERRAL BLOCKED: User {new_user.id} already referred")
                return result

            result['valid'] = True
            result['referral_code_obj'] = code_obj

        except ReferralCode.DoesNotExist:
            result['error'] = "Invalid referral code"
        except Exception as e:
            result['error'] = f"Validation error: {str(e)}"
            logger.error(f"Error validating referral code: {e}")

        return result

    @staticmethod
    @transaction.atomic
    def create_referral_safe(referral_code_obj, new_user) -> 'Referral':
        """
        Safely create referral relationship

        Args:
            referral_code_obj: Validated ReferralCode object
            new_user: The new user being referred

        Returns:
            Referral: Created referral object

        Raises:
            ReferralSafetyError: If safety check fails
        """
        referrer = referral_code_obj.user

        # Final safety checks
        ReferralValidator.validate_self_referral(referrer.id, new_user.id)
        ReferralValidator.validate_no_existing_referral(new_user.id)

        # Create referral
        referral = Referral.objects.create(
            referrer=referrer,
            referred_user=new_user,
            status='completed',  # or 'pending' if you want verification
            discount_used=False,
            reward_created=False
        )

        logger.info(f"Referral created: {referrer.id} -> {new_user.id} (ID: {referral.id})")

        return referral


# Example integration in signup view:
"""
from apps.growth.signup_safety import SignupReferralSafety

class SignupView(View):
    def post(self, request):
        # ... create user ...

        referral_code = request.POST.get('referral_code')

        if referral_code and user:
            # Validate
            validation = SignupReferralSafety.validate_referral_code(referral_code, user)

            if validation['valid']:
                try:
                    referral = SignupReferralSafety.create_referral_safe(
                        validation['referral_code_obj'],
                        user
                    )
                    messages.success(request, "Referral applied! You'll get a discount on your first payment.")
                except ReferralSafetyError as e:
                    logger.warning(f"Referral creation failed: {e}")
                    # Don't block signup, just log it
            else:
                if validation['error']:
                    messages.warning(request, f"Referral code: {validation['error']}")

        # ... continue signup ...
"""
