"""Welcome sequence: day-1 tips, day-3 check-in for new signups.

Rides existing wiring only: PeriodicJob (admin-scheduled, daily) -> this
handler -> NotificationService email (+Telegram DM when linked) -> durable
Event dedupe (once per user per step). No new frameworks.
"""
import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.events.models import record_event

logger = logging.getLogger(__name__)

DAY1 = "welcome.day1"
DAY3 = "welcome.day3"


def _send(user, template, subject, context, telegram_text):
    from apps.notifications.services import NotificationService
    from apps.bot_integration.transactional import TransactionalTelegramService
    try:
        if getattr(user, "email", None):
            NotificationService.send_email(user.email, template, subject, context)
    except Exception:
        logger.exception("welcome email failed for %s", user.pk)
    try:
        TransactionalTelegramService.send_text(user, telegram_text)
    except Exception:
        logger.exception("welcome telegram failed for %s", user.pk)


def _offered(user, step):
    _, created = record_event(
        step, dedupe_key="{}:{}".format(step, user.pk), user_id=user.pk,
        object_ref="user:{}".format(user.pk), payload={})
    return created


def _day1(user):
    if not _offered(user, DAY1):
        return False
    _send(user, "growth/email/welcome_day1",
          "Welcome! Tips to get the most from your account",
          {"username": user.username, "dashboard_url": "/dashboard/",
           "support_url": "/support/"},
          ("\U0001f44b Welcome, {}!\n\n"
           "3 quick things:\n"
           "1. Connect Telegram (dashboard) for channel access + alerts\n"
           "2. Pick a plan when you're ready\n"
           "3. Questions? /support/").format(user.username))
    return True


def _day3(user):
    from apps.subscriptions.models import Subscription
    from apps.bot_integration.models import TelegramAccount
    # Only nudge users who haven't bought AND haven't linked - the rest are fine.
    has_sub = Subscription.objects.filter(
        user=user, status=Subscription.Status.ACTIVE, is_active=True).exists()
    linked = TelegramAccount.objects.filter(user=user, is_active=True).exists()
    if has_sub or linked:
        return False
    if not _offered(user, DAY3):
        return False
    _send(user, "growth/email/welcome_day3",
          "Need a hand getting set up?",
          {"username": user.username, "support_url": "/support/",
           "connect_url": "/bot/telegram/connect/"},
          ("\U0001f9e9 Hi {}, stuck? Two-minute setup:\n"
           "Dashboard -> Connect Telegram -> pick a plan.\n"
           "Or just reply here / see /support/ and we'll help.").format(
               user.username))
    return True


def run_welcome_sequence(periodic_job):
    """Daily: day-1 for signups 24-48h ago; day-3 for 72-96h ago."""
    from apps.payments.models import PaymentIntent  # noqa: F401  (parity with winback)
    now = timezone.now()
    User = get_user_model()
    sent = 0
    day1_users = User.objects.filter(
        date_joined__gte=now - timezone.timedelta(hours=48),
        date_joined__lt=now - timezone.timedelta(hours=24))
    for u in day1_users.iterator():
        try:
            if _day1(u):
                sent += 1
        except Exception:
            logger.exception("welcome day1 failed for %s", u.pk)
    day3_users = User.objects.filter(
        date_joined__gte=now - timezone.timedelta(hours=96),
        date_joined__lt=now - timezone.timedelta(hours=72))
    for u in day3_users.iterator():
        try:
            if _day3(u):
                sent += 1
        except Exception:
            logger.exception("welcome day3 failed for %s", u.pk)
    return sent
