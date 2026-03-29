"""
Growth app adapters for signup integration.

Phase 4 (Viral Mode):
- Referral can be applied at signup OR before first paid purchase
- Added support for applying referral code to existing users
"""
import logging

from django.http import HttpResponseRedirect
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

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


def apply_referral_to_existing_user(request, user, code):
    """
    Apply a referral code to an existing user (before first purchase).

    This allows users who signed up without a referral code to apply one
    later, as long as they haven't made their first paid purchase yet.

    Args:
        request: HTTP request
        user: User to apply referral to
        code: Referral code string

    Returns:
        tuple: (success: bool, message: str)
    """
    from .services import ReferralService

    # Check if user can apply referral
    if not ReferralService.can_apply_referral(user):
        # Check why they can't apply
        from .models import Referral
        has_referrer = Referral.objects.filter(referred_user=user).exists()
        if has_referrer:
            return (False, _("You already have a referrer and cannot change it."))
        else:
            return (False, _("You already have an active subscription and cannot apply a referral code."))

    # Try to record the referral
    referral = ReferralService.record_referral_signup(user, code)

    if referral:
        logger.info(f"Referral applied to existing user {user.id}")
        return (True, _("Referral code applied successfully! Your referrer will be rewarded when you make your first purchase."))
    else:
        return (False, _("Invalid referral code or self-referral not allowed."))


try:
    from allauth.account.adapter import DefaultAccountAdapter

    class GrowthAccountAdapter(ReferralSignupAdapter, DefaultAccountAdapter):
        """Allauth adapter with referral support."""
        pass

except ImportError:
    GrowthAccountAdapter = None
