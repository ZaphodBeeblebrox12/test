"""
Growth signals for auto-processing pending claims.

This module connects to auth signals to automatically process
gift claims when users sign up or log in.
"""
import logging

from django.dispatch import receiver
from django.contrib.auth import user_logged_in
from allauth.account.signals import user_signed_up

from .views import ProcessPendingClaimsView

logger = logging.getLogger(__name__)


@receiver(user_logged_in)
def process_pending_claims_on_login(sender, request, user, **kwargs):
    """
    Process any pending gift claims when a user logs in.

    This handles the case where an anonymous user clicked a gift
    link, was redirected to login, and is now authenticating.
    """
    try:
        subscription = ProcessPendingClaimsView.process_for_request(request)
        if subscription:
            logger.info(
                f"Auto-processed pending gift claim for user {user.id} "
                f"on login, subscription {subscription.id}"
            )
    except Exception as e:
        logger.error(f"Error processing pending claims on login: {e}")


@receiver(user_signed_up)
def process_pending_claims_on_signup(sender, request, user, **kwargs):
    """
    Process any pending gift claims when a user signs up.

    This handles the case where an anonymous user clicked a gift
    link, was redirected to signup, and has now created an account.
    """
    try:
        subscription = ProcessPendingClaimsView.process_for_request(request)
        if subscription:
            logger.info(
                f"Auto-processed pending gift claim for user {user.id} "
                f"on signup, subscription {subscription.id}"
            )
    except Exception as e:
        logger.error(f"Error processing pending claims on signup: {e}")
