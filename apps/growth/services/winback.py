"""Win-back: offer a one-time coupon to subscriptions that expired ~3 days ago.

Rides EXCLUSIVELY on existing wiring: PeriodicJob (admin-scheduled) -> this
handler -> promotions.Coupon (validated by the existing checkout flow) ->
NotificationService email + TransactionalTelegramService DM -> durable Event
(dedupe). No checkout changes: the user repurchases with the code and the
standard coupon validation applies.

Anti-coupon-training guards (deliberate): offered ONCE per customer ever
(Event dedupe), never while they hold an active subscription, 7-day validity,
max 1 redemption.
"""
import logging
import secrets

from django.utils import timezone

from apps.events.models import Event, record_event
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Subscription

logger = logging.getLogger(__name__)

OFFERED = "winback.offered"
WINDOW_DAYS = 3          # target: expired 3 days ago (swept once daily)
VALID_DAYS = 7
PERCENT_OFF = 20


def _last_currency(user_id):
    last = (PaymentIntent.objects
            .filter(user_id=user_id, status=PaymentIntent.Status.SUCCESS)
            .order_by("-created_at").values_list("currency", flat=True).first())
    return last or "USD"


def _send_offer(user, coupon):
    """Email + Telegram; never raises; idempotent-by-caller (event dedupe)."""
    from apps.notifications.services import NotificationService
    from apps.bot_integration.transactional import TransactionalTelegramService
    context = {"username": user.username, "code": coupon.code,
               "percent_off": coupon.percent_off,
               "valid_until": coupon.valid_until}
    text = (
        "\U0001f3af Come back, {}! Your {}% discount code: {}\n\n"
        "Valid until {} - apply it at checkout."
    ).format(user.username, coupon.percent_off, coupon.code,
             coupon.valid_until.strftime("%b %d"))
    try:
        if getattr(user, "email", None):
            NotificationService.send_email(
                user.email, "growth/email/winback",
                "We'd love you back - {}% off".format(coupon.percent_off),
                context)
    except Exception:
        logger.exception("winback email failed for %s", user.pk)
    try:
        TransactionalTelegramService.send_text(user, text)
    except Exception:
        logger.exception("winback telegram failed for %s", user.pk)


def maybe_offer_user(user):
    """All guards + offer for one user. Returns True iff offered now."""
    from apps.promotions.models import Coupon

    if Subscription.objects.filter(
            user=user, status=Subscription.Status.ACTIVE, is_active=True).exists():
        return False  # returned already or still active
    # Claim the once-per-customer slot BEFORE creating anything (atomic dedupe).
    from apps.events.models import Event as _E
    claim_key = "winback.offered:{}".format(user.pk)
    _ev, created = record_event(
        OFFERED, dedupe_key=claim_key, user_id=user.pk,
        object_ref="user:{}".format(user.pk), payload={"status": "claim"})
    if not created:
        return False  # offered before (once per customer, ever)
    now = timezone.now()
    coupon = Coupon.objects.create(
        code="WELCOME{}".format(secrets.token_hex(4).upper()),
        name="Win-back offer ({}% off)".format(PERCENT_OFF),
        discount_type=Coupon.DiscountType.PERCENT,
        percent_off=PERCENT_OFF,
        currency=_last_currency(user.pk),
        valid_from=now,
        valid_until=now + timezone.timedelta(days=VALID_DAYS),
        max_redemptions=1,
        max_per_user=1,
        active=True,
    )
    _E.objects.filter(pk=_ev.pk).update(payload={
        "code": coupon.code, "valid_until": coupon.valid_until.isoformat()})
    _send_offer(user, coupon)
    return True


def run_win_back(periodic_job):
    """PeriodicJob handler: sweep subscriptions expired ~WINDOW_DAYS ago."""
    now = timezone.now()
    start = now - timezone.timedelta(days=WINDOW_DAYS + 1)
    end = now - timezone.timedelta(days=WINDOW_DAYS)
    expired_users = (Subscription.objects
                     .filter(status=Subscription.Status.EXPIRED, is_active=False,
                             expires_at__gte=start, expires_at__lt=end)
                     .values_list("user", flat=True).distinct())
    from django.contrib.auth import get_user_model
    User = get_user_model()
    offered = 0
    for uid in expired_users:
        try:
            if maybe_offer_user(User.objects.get(pk=uid)):
                offered += 1
        except Exception:
            logger.exception("winback failed for user %s", uid)
    return offered
