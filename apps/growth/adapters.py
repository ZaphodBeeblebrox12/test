"""
Growth app adapters for signup integration.
"""
import logging

logger = logging.getLogger(__name__)


class ReferralSignupAdapter:
    """
    Mixin/Adapter for handling referral code during signup.

    IMPORTANT: This only RECORDS the referral (creates pending referral).
    Completion and reward creation happen ONLY after purchase.
    """

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit)
        self._process_referral_code(request, user)
        return user

    def _process_referral_code(self, request, user):
        from .services import ReferralService

        referral_code = request.session.pop("referral_code", None)

        if referral_code:
            referral = ReferralService.record_referral_signup(user, referral_code)
            if referral:
                logger.info(f"Referral recorded (pending) for user {user.id}")

        request.session.pop("pending_referral", None)
        request.session.modified = True


def process_referral_on_signup(request, user):
    """Utility function to process referral code during signup."""
    from .services import ReferralService

    referral_code = request.session.pop("referral_code", None)

    if referral_code:
        referral = ReferralService.record_referral_signup(user, referral_code)
        if referral:
            logger.info(f"Referral recorded (pending) for user {user.id}")

    request.session.pop("pending_referral", None)
    request.session.modified = True


try:
    from allauth.account.adapter import DefaultAccountAdapter

    class GrowthAccountAdapter(ReferralSignupAdapter, DefaultAccountAdapter):
        """Allauth adapter with referral support."""
        pass

except ImportError:
    GrowthAccountAdapter = None
