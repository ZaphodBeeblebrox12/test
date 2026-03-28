"""
Growth app adapters for signup integration.

This module handles referral code recording during signup.
Referrals are NOT completed here - they complete on purchase only.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class ReferralSignupAdapter:
    """
    Mixin/Adapter for handling referral code during signup.
    Works with allauth or custom signup views.

    IMPORTANT: This only RECORDS the referral (creates pending referral).
    Completion happens ONLY after purchase via ReferralService.complete_referral_on_purchase()
    """

    def save_user(self, request, user, form, commit=True):
        """
        Hook for allauth adapter.
        Also works as standalone method for custom views.
        """
        # Save user first
        user = super().save_user(request, user, form, commit)

        # Process referral code from session (records only, does NOT complete)
        self._process_referral_code(request, user)

        return user

    def _process_referral_code(self, request, user):
        """Record referral code if present in session."""
        from .services import ReferralService

        referral_code = request.session.pop("referral_code", None)

        if referral_code:
            referral = ReferralService.record_referral_signup(user, referral_code)
            if referral:
                logger.info(f"Referral recorded (pending) for user {user.id}")
            else:
                logger.warning(f"Failed to record referral for user {user.id}")

        # Clean up any legacy session keys
        request.session.pop("pending_referral", None)
        request.session.modified = True


def process_referral_on_signup(request, user):
    """
    Utility function to process referral code during signup.
    Call this in your signup view if not using the adapter mixin.

    Args:
        request: HTTP request with session
        user: Newly created user
    """
    from .services import ReferralService

    referral_code = request.session.pop("referral_code", None)

    if referral_code:
        referral = ReferralService.record_referral_signup(user, referral_code)
        if referral:
            logger.info(f"Referral recorded (pending) for user {user.id}")

    # Clean up legacy session key
    request.session.pop("pending_referral", None)
    request.session.modified = True


# For allauth integration
try:
    from allauth.account.adapter import DefaultAccountAdapter

    class GrowthAccountAdapter(ReferralSignupAdapter, DefaultAccountAdapter):
        """
        Allauth adapter with referral support.

        In settings.py:
            ACCOUNT_ADAPTER = "apps.growth.adapters.GrowthAccountAdapter"

        Referral is recorded on signup, completed on purchase.
        """
        pass

except ImportError:
    # allauth not installed, skip
    GrowthAccountAdapter = None
