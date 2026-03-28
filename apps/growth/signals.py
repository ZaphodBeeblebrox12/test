"""
Growth app signals.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings

from .models import ReferralCode

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_referral_code(sender, instance, created, **kwargs):
    """
    Auto-create referral code when user signs up.
    Non-blocking - logs error but doesn't fail signup if creation fails.
    """
    if created:
        try:
            ReferralCode.objects.get_or_create(
                user=instance,
                defaults={"code": ReferralCode.generate_unique_code()}
            )
            logger.info(f"Referral code created for user {instance.id}")
        except Exception as e:
            # Log but don't break signup flow
            logger.error(f"Failed to create referral code for user {instance.id}: {e}")


# NOTE: Referral completion on email confirmation has been REMOVED.
# Referrals are now completed ONLY after successful purchase.
# See ReferralService.complete_referral_on_purchase() in services.py
