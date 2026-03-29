"""
Celery tasks for the growth app.

Includes:
- Referral reward unlock task (runs periodically)
"""
from celery import shared_task
import logging

from .services import ReferralRewardService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def unlock_pending_referral_rewards(self):
    """
    Process pending referral rewards that are ready to unlock.

    This task:
    1. Finds rewards past their unlock delay
    2. Checks if triggering subscription was refunded using explicit link
    3. If refunded → marks EXPIRED
    4. If not refunded → credits wallet and creates ledger entry

    Should be scheduled to run periodically (e.g., every hour).

    Returns:
        dict: {"processed": int, "errors": int}
    """
    try:
        processed = ReferralRewardService.unlock_eligible_rewards()
        logger.info(f"Processed {processed} pending referral rewards")
        return {"processed": processed, "errors": 0}
    except Exception as exc:
        logger.error(f"Error unlocking referral rewards: {exc}")
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
