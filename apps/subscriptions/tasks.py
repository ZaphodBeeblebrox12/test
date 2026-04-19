import logging
from celery import shared_task
from .geoip import check_and_update_database

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1)
def update_maxmind_database_task(self):
    """
    Celery periodic task to download and update the MaxMind GeoLite2 database.
    """
    logger.info("Running MaxMind database update task...")
    try:
        success = check_and_update_database(force=False)
        if success:
            logger.info("MaxMind database update completed successfully.")
        else:
            logger.warning("MaxMind database update failed or skipped.")
    except Exception as e:
        logger.exception(f"MaxMind database update error: {e}")
        # Optional: retry once after a delay
        raise self.retry(exc=e, countdown=3600)