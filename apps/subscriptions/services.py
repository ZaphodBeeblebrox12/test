"""
Subscription services for geo pricing, gifts, admin grants, and events.
"""
import uuid
from typing import Optional, Dict, Any

from django.utils import timezone

from apps.events.models import record_event
from django.conf import settings
from django.db import transaction
from django.core.exceptions import PermissionDenied
from ipware import get_client_ip

from apps.accounts.models import User
from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    UpgradeHistory, GiftSubscription, GeoPlanPrice, UserTrialUsage,
    SubscriptionReminder,
)

COUNTRY_TO_REGION = {
    # APAC
    "IN": "APAC", "CN": "APAC", "JP": "APAC", "KR": "APAC", "SG": "APAC",
    "AU": "APAC", "NZ": "APAC", "TH": "APAC", "VN": "APAC", "MY": "APAC",
    "ID": "APAC", "PH": "APAC", "HK": "APAC", "TW": "APAC", "BD": "APAC",
    "PK": "APAC", "LK": "APAC", "NP": "APAC",
    # EU
    "DE": "EU", "FR": "EU", "IT": "EU", "ES": "EU", "NL": "EU", "BE": "EU",
    "AT": "EU", "PT": "EU", "GR": "EU", "IE": "EU", "FI": "EU", "SE": "EU",
    "DK": "EU", "NO": "EU", "PL": "EU", "CZ": "EU", "HU": "EU", "RO": "EU",
    "BG": "EU", "HR": "EU", "SI": "EU", "SK": "EU", "EE": "EU", "LV": "EU",
    "LT": "EU", "LU": "EU", "MT": "EU", "CY": "EU", "CH": "EU", "GB": "EU",
    # NA
    "US": "NA", "CA": "NA", "MX": "NA",
    # LATAM
    "BR": "LATAM", "AR": "LATAM", "CL": "LATAM", "CO": "LATAM", "PE": "LATAM",
    "VE": "LATAM", "EC": "LATAM", "BO": "LATAM", "PY": "LATAM", "UY": "LATAM",
    "CR": "LATAM", "PA": "LATAM", "GT": "LATAM", "HN": "LATAM", "SV": "LATAM",
    "NI": "LATAM", "DO": "LATAM", "JM": "LATAM", "TT": "LATAM",
    # MENA
    "AE": "MENA", "SA": "MENA", "QA": "MENA", "KW": "MENA", "BH": "MENA",
    "OM": "MENA", "JO": "MENA", "LB": "MENA", "IL": "MENA", "EG": "MENA",
    "MA": "MENA", "TN": "MENA", "DZ": "MENA", "LY": "MENA", "IQ": "MENA",
    "IR": "MENA", "TR": "MENA",
    # SSA
    "ZA": "SSA", "NG": "SSA", "KE": "SSA", "GH": "SSA", "UG": "SSA",
    "TZ": "SSA", "ZW": "SSA", "ZM": "SSA", "MW": "SSA", "MZ": "SSA",
    "NA": "SSA", "BW": "SSA", "SZ": "SSA", "LS": "SSA", "RW": "SSA",
    "ET": "SSA", "SN": "SSA", "CI": "SSA", "CM": "SSA", "AO": "SSA",
    "CD": "SSA", "CG": "SSA", "GA": "SSA", "GQ": "SSA",
}

def get_region_for_country(country_code: str) -> Optional[str]:
    return COUNTRY_TO_REGION.get(country_code.upper()) if country_code else None

def get_request_country(request) -> str:
    import logging
    logger = logging.getLogger(__name__)
    cf_country = request.META.get("HTTP_CF_IPCOUNTRY", "").upper()
    if cf_country and cf_country != "XX" and len(cf_country) == 2:
        return cf_country
    client_ip, is_routable = get_client_ip(request)
    if client_ip:
        if is_routable:
            logger.debug(f"IP detected ({client_ip}) but no geo mapping available")
        else:
            logger.debug(f"Private/non-routable IP detected ({client_ip})")
    default_country = getattr(settings, "DEFAULT_COUNTRY", "US")
    return default_country.upper()

def get_pricing_country(request) -> Optional[str]:
    """
    Determine pricing country with fallback chain:
    1. test_country (DEBUG only)
    2. Cloudflare CF-IPCountry header
    3. MaxMind GeoLite2 lookup (NEW)
    4. Return None (global pricing)
    """
    if getattr(settings, "DEBUG", False):
        test_country = request.GET.get("test_country")
        if test_country:
            return test_country.upper()

    # Primary: Cloudflare header
    cf_country = request.META.get("HTTP_CF_IPCOUNTRY", "").upper()
    if cf_country and cf_country != "XX" and len(cf_country) == 2:
        return cf_country

    # Fallback: MaxMind GeoLite2
    try:
        from .geoip import get_country_from_maxmind
        maxmind_country = get_country_from_maxmind(request)
        if maxmind_country:
            return maxmind_country.upper()
    except Exception:
        pass

    # Final fallback: None (triggers global pricing)
    return None


def split_resolved_price(resolved):
    """Route a resolved price to the correct FK and an immutable snapshot.
    Returns a dict suitable for Subscription/PaymentIntent creation/update."""
    from apps.subscriptions.models import GeoPlanPrice as _GP
    base = {
        "price_cents": resolved.price_cents,
        "price_currency": resolved.currency,
    }
    if isinstance(resolved, _GP):
        base["geo_plan_price"] = resolved
        base["plan_price"] = None
    else:
        base["plan_price"] = resolved
        base["geo_plan_price"] = None
    return base

def resolve_plan_price(plan: Plan, interval: str, request):
    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None
    if country:
        try:
            return GeoPlanPrice.objects.get(
                plan=plan, interval=interval, country=country.upper(), is_active=True
            )
        except GeoPlanPrice.DoesNotExist:
            pass
    if region:
        try:
            return GeoPlanPrice.objects.get(
                plan=plan, interval=interval, region=region, country__isnull=True, is_active=True
            )
        except GeoPlanPrice.DoesNotExist:
            pass
    try:
        return PlanPrice.objects.get(plan=plan, interval=interval, is_active=True)
    except PlanPrice.DoesNotExist:
        raise PlanPrice.DoesNotExist(f"No active price for plan '{plan.name}' interval '{interval}'")

# ------------------------------------------------------------------------------
# NEW HELPER FUNCTIONS FOR VIEWS
# ------------------------------------------------------------------------------

def format_price(price_cents: int, currency: str = "USD") -> str:
    """Format price in cents to a human-readable string."""
    symbols = {'USD': '$', 'EUR': '€', 'GBP': '£', 'INR': '₹', 'JPY': '¥'}
    symbol = symbols.get(currency, currency)
    dollars = price_cents / 100
    if dollars.is_integer():
        return f"{symbol}{int(dollars)}"
    return f"{symbol}{dollars:.2f}"

def has_user_used_trial(user, plan):
    """Check if a user has already used a specific trial plan."""
    if not user or not user.is_authenticated:
        return False
    return UserTrialUsage.objects.filter(user=user, plan=plan).exists()

def get_geo_price_for_trial(plan, country):
    """Get the GeoPlanPrice for a trial plan for a given country."""
    if not country:
        return None
    try:
        return GeoPlanPrice.objects.get(
            plan=plan, country=country.upper(), is_active=True
        )
    except GeoPlanPrice.DoesNotExist:
        return None

def purchase_plan(user, plan, request=None):
    """Create a subscription for the user (handles both regular and trial plans)."""
    from datetime import timedelta

    if plan.is_hidden:
        raise PermissionDenied(
            "This plan is granted by admin approval and cannot be purchased.")

    if not plan.is_trial:
        raise PermissionDenied(
            "Paid plans must be purchased through the payment flow.")

    # Check trial usage if it's a trial plan
    if plan.is_trial:
        if has_user_used_trial(user, plan):
            raise PermissionDenied("You have already used this trial.")
        # Verify geo price exists (region lock)
        country = get_pricing_country(request) if request else None
        if not get_geo_price_for_trial(plan, country):
            raise PermissionDenied("This trial is not available in your region.")

    # Determine expiry (PAID plans never reach this -- they are routed to the
    # payment flow by purchase_plan_view; see the guard there.)
    if plan.is_trial:
        expires_at = timezone.now() + timedelta(days=plan.trial_duration_days)
    else:
        raise PermissionDenied(
            "Paid plans must be purchased through the payment flow.")

    # Get pricing country/region for record keeping
    pricing_country = get_pricing_country(request) if request else None
    pricing_region = get_region_for_country(pricing_country) if pricing_country else None

    # Deactivate any existing active subscriptions
    Subscription.objects.filter(user=user, is_active=True).update(
        is_active=False, status=Subscription.Status.CANCELED, canceled_at=timezone.now()
    )

    # Create the subscription
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        status=Subscription.Status.ACTIVE,
        is_active=True,
        started_at=timezone.now(),
        expires_at=expires_at,
        is_trial=plan.is_trial,
        pricing_country=pricing_country,
        pricing_region=pricing_region,
    )

    # Record trial usage if applicable
    if plan.is_trial:
        UserTrialUsage.objects.create(
            user=user,
            plan=plan,
            subscription=subscription,
            expires_at=expires_at,
        )

    # Create history record
    event_type = SubscriptionHistory.EventType.TRIAL_STARTED if plan.is_trial else SubscriptionHistory.EventType.CREATED
    SubscriptionHistory.objects.create(
        subscription=subscription,
        user=user,
        event_type=event_type,
        new_plan_id=plan.id,
        new_status=subscription.status,
        notes=f"{'Trial' if plan.is_trial else 'Subscription'} started"
    )
    if plan.is_trial:
        record_event(
            "trial.started",
            dedupe_key=f"trial.started:{subscription.pk}",
            user_id=user.id,
            object_ref=f"subscription:{subscription.pk}",
            payload={"subscription_id": str(subscription.pk),
                     "trial_duration_days": plan.trial_duration_days},
        )

    return subscription

# ------------------------------------------------------------------------------
# EXISTING GIFT & ADMIN FUNCTIONS (unchanged)
# ------------------------------------------------------------------------------

def create_gift_subscription(
    from_user: User,
    plan: Plan,
    duration_days: int = 30,
    message: str = "",
    request = None
) -> GiftSubscription:
    """Create a gift subscription."""
    gift = GiftSubscription.objects.create(
        plan=plan,
        from_user=from_user,
        message=message,
        gift_code=str(uuid.uuid4())[:16].upper(),
        duration_days=duration_days,
        expires_at=timezone.now() + timezone.timedelta(days=30),
    )
    return gift

def claim_gift_subscription(
    gift_code: str,
    to_user: User,
    request = None
) -> Subscription:
    gift = GiftSubscription.objects.get(
        gift_code=gift_code.upper(),
        status=GiftSubscription.Status.PENDING
    )
    if gift.expires_at < timezone.now():
        gift.status = GiftSubscription.Status.EXPIRED
        gift.save()
        raise ValueError("Gift code has expired")
    if gift.to_user:
        raise ValueError("Gift already claimed")
    with transaction.atomic():
        expires_at = timezone.now() + timezone.timedelta(days=gift.duration_days)
        subscription = Subscription.objects.create(
            user=to_user,
            plan=gift.plan,
            plan_price=gift.plan_price,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            is_gift=True,
            gift_from=gift.from_user,
            gift_message=gift.message,
        )
        gift.to_user = to_user
        gift.status = GiftSubscription.Status.CLAIMED
        gift.claimed_at = timezone.now()
        gift.resulting_subscription = subscription
        gift.save()
        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=to_user,
            event_type=SubscriptionHistory.EventType.GIFT_RECEIVED,
            new_plan_id=gift.plan.id,
            new_status=subscription.status,
            metadata={
                "gift_id": str(gift.id),
                "from_user_id": str(gift.from_user.id),
                "from_username": gift.from_user.username,
            },
            notes=f"Claimed gift from {gift.from_user.username}"
        )
        emit_event(
            event_type="SUBSCRIPTION_CREATED",
            subscription=subscription,
            user=to_user,
            metadata={"source": "gift", "gift_id": str(gift.id)}
        )
        return subscription

def grant_subscription_by_admin(
    user: User,
    plan: Plan,
    granted_by: User,
    duration_days: int | None = 30,
    reason: str = "",
    request = None
) -> Subscription:
    with transaction.atomic():
        if duration_days is None:
            expires_at = None  # no expiry (approval-granted hidden plans)
        else:
            expires_at = timezone.now() + timezone.timedelta(days=duration_days)
        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            is_admin_grant=True,
            granted_by=granted_by,
            grant_reason=reason,
        )
        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.ADMIN_GRANTED,
            new_plan_id=plan.id,
            new_status=subscription.status,
            metadata={
                "granted_by_id": str(granted_by.id),
                "granted_by_username": granted_by.username,
                "reason": reason
            },
            notes=f"Admin grant by {granted_by.username}: {plan.name}"
        )
        emit_event(
            event_type="ADMIN_GRANTED_PLAN",
            subscription=subscription,
            user=user,
            metadata={
                "granted_by_id": str(granted_by.id),
                "granted_by_username": granted_by.username,
                "reason": reason
            }
        )
        return subscription

def emit_event(
    event_type: str,
    subscription: Subscription,
    user: User,
    metadata: Dict[str, Any] = None
):
    import logging
    event_data = {
        "event_type": event_type,
        "subscription_id": str(subscription.id) if subscription else None,
        "user_id": str(user.id),
        "timestamp": timezone.now().isoformat(),
        "metadata": metadata or {}
    }
    logger = logging.getLogger(__name__)
    logger.info(f"SUBSCRIPTION_EVENT: {event_type} - {event_data}")

def start_trial(user: User, plan: Plan, days: int = 14, request = None) -> Subscription:
    with transaction.atomic():
        expires_at = timezone.now() + timezone.timedelta(days=days)
        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider="trial",
        )
        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.TRIAL_STARTED,
            new_plan_id=plan.id,
            new_status=subscription.status,
            metadata={"trial_days": days},
            notes=f"Started {days}-day trial"
        )
        emit_event(
            event_type="TRIAL_STARTED",
            subscription=subscription,
            user=user,
            metadata={"trial_days": days}
        )
        return subscription

def expire_trial(subscription: Subscription):
    if subscription.payment_provider != "trial":
        raise ValueError("Only trial subscriptions can be expired this way")
    subscription.status = Subscription.Status.EXPIRED
    subscription.is_active = False
    subscription.save()
    SubscriptionHistory.objects.create(
        subscription=subscription,
        user=subscription.user,
        event_type=SubscriptionHistory.EventType.TRIAL_EXPIRED,
        previous_status=Subscription.Status.ACTIVE,
        new_status=Subscription.Status.EXPIRED,
        notes="Trial expired"
    )
    emit_event(
        event_type="TRIAL_EXPIRED",
        subscription=subscription,
        user=subscription.user,
        metadata={"trial_ended": True}
    )

# ---------------------------------------------------------------------------
# P2: subscription lifecycle — expiry enforcement + user cancellation
#
# Both operations use an ATOMIC CONDITIONAL UPDATE as the single claim:
#   UPDATE subscription SET status=..., is_active=False
#     WHERE id=<pk> AND is_active AND status='active' [AND expires_at <= now]
# Exactly one concurrent caller wins (rows-affected == 1); everyone else sees
# 0 and writes nothing.  History + reconcile enqueue therefore happen at most
# once per subscription.  Instance .save() is deliberately NOT used: it cannot
# provide this guarantee, and the queryset UPDATE intentionally bypasses the
# post_save signal (reconcile is enqueued EXPLICITLY below, outbox-style).
# ---------------------------------------------------------------------------


def expire_subscription(subscription, now=None):
    """Expire ONE subscription whose expires_at has passed.

    Returns True if this call performed the expiry, False otherwise
    (not expired yet, or already expired/canceled by an earlier run).
    Idempotent and safe under concurrent/repeated execution.
    """
    from apps.jobs.enqueue import enqueue_reconcile

    now = now or timezone.now()
    claimed = Subscription.objects.filter(
        pk=subscription.pk,
        is_active=True,
        status=Subscription.Status.ACTIVE,
        expires_at__isnull=False,
        expires_at__lte=now,
    ).update(status=Subscription.Status.EXPIRED, is_active=False)
    if not claimed:
        return False
    # Claim won: write history + schedule reconciliation exactly once.
    with transaction.atomic():
        SubscriptionHistory.objects.create(
            subscription_id=subscription.pk,
            user_id=subscription.user_id,
            event_type=SubscriptionHistory.EventType.EXPIRED,
            previous_status=Subscription.Status.ACTIVE,
            new_status=Subscription.Status.EXPIRED,
            notes="Subscription expired (expires_at reached).",
        )
    record_event(
        "subscription.expired",
        dedupe_key=f"subscription.expired:{subscription.pk}",
        user_id=subscription.user_id,
        object_ref=f"subscription:{subscription.pk}",
        payload={"subscription_id": str(subscription.pk), "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else None},
    )
    # Entitlement source-of-truth is now inactive -> revoke downstream access.
    enqueue_reconcile(subscription.user_id, reason="subscription_expired")
    return True


def expire_due_subscriptions(now=None):
    """Expire every active subscription whose expires_at has passed.

    Sweeper entry point for the durable-jobs worker (kind 'subscription_expiry').
    Returns the number of subscriptions expired by THIS run.  Safe to run
    repeatedly and from multiple workers: per-row conditional claims prevent
    double expiry, and a replacement subscription (later expires_at) is never
    touched because the filter is per-row on its own expires_at.
    """
    now = now or timezone.now()
    due = Subscription.objects.filter(
        is_active=True,
        status=Subscription.Status.ACTIVE,
        expires_at__isnull=False,
        expires_at__lte=now,
    ).order_by("pk")
    count = 0
    for sub in due.iterator():
        if expire_subscription(sub, now=now):
            count += 1
    return count


PRE_EXPIRY_DAYS = 3


def _plan_label(subscription) -> str:
    plan = getattr(subscription, "plan", None)
    for attr in ("display_name", "name", "title"):
        value = getattr(plan, attr, None)
        if value:
            return str(value)
    return "your plan"


def _send_reminder_once(subscription, kind: str) -> bool:
    """Claim (get_or_create) then deliver on both channels. Returns True when
    at least one channel delivered; row is removed otherwise so a later run
    retries. Never raises."""
    _, created = SubscriptionReminder.objects.get_or_create(
        subscription=subscription, kind=kind)
    if not created:
        return False  # already delivered for this subscription+kind

    from apps.bot_integration import transactional as tg_tx
    from apps.bot_integration.transactional import TransactionalTelegramService

    username = getattr(subscription.user, "username", None) or "trader"
    plan_label = _plan_label(subscription)
    if kind == SubscriptionReminder.Kind.PRE_EXPIRY:
        tg_text = tg_tx.render_expiry_reminder(
            username, plan_label, PRE_EXPIRY_DAYS)
        email_tpl, subject = (
            "subscriptions/email/reminder_pre_expiry",
            f"Your {plan_label} subscription expires in {PRE_EXPIRY_DAYS} days")
    else:
        tg_text = tg_tx.render_access_removed(
            username, plan_label)
        email_tpl, subject = (
            "subscriptions/email/reminder_post_expiry",
            f"Your {plan_label} access has ended")

    delivered = False
    tg_message_id = None
    try:
        tg = TransactionalTelegramService.send_text(subscription.user, tg_text)
        if tg.sent:
            delivered = True
            tg_message_id = tg.message_id
    except Exception:  # channel failure must never break the sweep
        pass

    email_sent = False
    email_recipient = ""
    try:
        recipient = getattr(subscription.user, "email", None)
        if recipient:
            from apps.notifications.services import NotificationService
            NotificationService.send_email(
                to_email=recipient, template=email_tpl, subject=subject,
                context={"username": username, "plan_name": plan_label,
                         "days": PRE_EXPIRY_DAYS})
            email_sent = True
            delivered = True
            email_recipient = recipient
    except Exception:
        pass

    if delivered:
        SubscriptionReminder.objects.filter(
            subscription=subscription, kind=kind).update(
                telegram_message_id=tg_message_id, email_sent=email_sent,
                email_recipient=email_recipient)
        return True
    # No channel could deliver: release the claim so a later run retries.
    SubscriptionReminder.objects.filter(
        subscription=subscription, kind=kind).delete()
    return False


def send_expiry_reminders(now=None) -> int:
    """Periodic sweeper (kind 'expiry_reminders').

    PRE_EXPIRY : ACTIVE subscriptions expiring in the [3d, 4d) window that
                 have no pre-expiry reminder yet.
    POST_EXPIRY: EXPIRED subscriptions with no post-expiry reminder yet
                 (the expiry job has already run; reconcile revokes access).

    Dedupe: one reminder per subscription+kind (DB unique constraint plus
    get_or_create claim). Renewal before expiry moves expires_at out of the
    window, so a renewed subscription naturally never gets a stale reminder.
    Idempotent, safe to run repeatedly and from multiple workers.
    TRANSACTIONAL — never marketing; no consent gate.
    """
    now = now or timezone.now()
    from datetime import timedelta
    win_start = now + timedelta(days=PRE_EXPIRY_DAYS)
    win_end = now + timedelta(days=PRE_EXPIRY_DAYS + 1)
    pre = Subscription.objects.filter(
        status=Subscription.Status.ACTIVE, is_active=True,
        expires_at__gte=win_start, expires_at__lt=win_end,
    ).exclude(reminders__kind=SubscriptionReminder.Kind.PRE_EXPIRY
              ).order_by("pk")
    post = Subscription.objects.filter(
        status=Subscription.Status.EXPIRED, is_active=False,
    ).exclude(reminders__kind=SubscriptionReminder.Kind.POST_EXPIRY
              ).order_by("pk")
    sent = 0
    for sub in pre.iterator():
        if _send_reminder_once(sub, SubscriptionReminder.Kind.PRE_EXPIRY):
            sent += 1
    for sub in post.iterator():
        if _send_reminder_once(sub, SubscriptionReminder.Kind.POST_EXPIRY):
            sent += 1
    return sent


def cancel_subscription(subscription, now=None, actor="user"):
    """Cancel an ACTIVE subscription: access ends IMMEDIATELY.

    Chosen semantics (no provider subscriptions/renewals exist yet):
      - status  -> CANCELED, is_active -> False, canceled_at -> now
      - access ends NOW (not at expires_at): nothing in the system would
        later flip is_active, so deferred cancellation would silently keep
        granting entitlement past the user's request.
      - exactly one SubscriptionHistory(CANCELED) per subscription
      - Telegram reconciliation is enqueued to revoke downstream access
      - idempotent: repeating the call on an already canceled/expired
        subscription is a no-op returning False.
    """
    from apps.jobs.enqueue import enqueue_reconcile

    now = now or timezone.now()
    claimed = Subscription.objects.filter(
        pk=subscription.pk,
        is_active=True,
        status=Subscription.Status.ACTIVE,
    ).update(status=Subscription.Status.CANCELED,
             is_active=False,
             canceled_at=now)
    if not claimed:
        return False
    with transaction.atomic():
        SubscriptionHistory.objects.create(
            subscription_id=subscription.pk,
            user_id=subscription.user_id,
            event_type=SubscriptionHistory.EventType.CANCELED,
            previous_status=Subscription.Status.ACTIVE,
            new_status=Subscription.Status.CANCELED,
            notes=f"Subscription canceled by {actor}.",
        )
    record_event(
        "subscription.canceled",
        dedupe_key=f"subscription.canceled:{subscription.pk}:{actor}",
        user_id=subscription.user_id,
        object_ref=f"subscription:{subscription.pk}",
        payload={"subscription_id": str(subscription.pk), "actor": actor},
    )
    enqueue_reconcile(subscription.user_id, reason="subscription_canceled")
    return True


def extend_subscription(subscription, days, actor="admin"):
    """Extend an ACTIVE subscription's entitlement by `days`, from TODAY.

    Fills the small operational gap left by cancel/expire/grant.  No new
    payment is created and no PaymentIntent is touched -- this is an admin
    entitlement extension, not a purchase.  Uses the same atomic conditional
    claim as expire/cancel so it is idempotent and race-safe.  Records the
    extension with the EXISTING SubscriptionHistory.EventType.RENEWED
    ("Renewed"), which is the semantically correct event for extending an
    entitlement period without a new payment (the audit model has no separate
    "extended" event, and inventing one would break future reporting).

    Only ACTIVE subscriptions can be extended; canceled/expired rows are left
    untouched (returns False).  The plan/price/payment snapshot is unchanged.
    """
    from apps.jobs.enqueue import enqueue_reconcile

    new_expiry = timezone.now() + timezone.timedelta(days=days)
    claimed = Subscription.objects.filter(
        pk=subscription.pk,
        is_active=True,
        status=Subscription.Status.ACTIVE,
    ).update(expires_at=new_expiry)
    if not claimed:
        return False
    with transaction.atomic():
        SubscriptionHistory.objects.create(
            subscription_id=subscription.pk,
            user_id=subscription.user_id,
            event_type=SubscriptionHistory.EventType.RENEWED,
            previous_status=Subscription.Status.ACTIVE,
            new_status=Subscription.Status.ACTIVE,
            notes=f"Subscription extended by {days} day(s) by {actor}.",
        )
    # Entitlement unchanged (still active) but reconcile is cheap/idempotent;
    # enqueue so any access drift is healed.
    record_event(
        "renewal.completed",
        dedupe_key=f"renewal.completed:{subscription.pk}:{subscription.expires_at.isoformat()}",
        user_id=subscription.user_id,
        object_ref=f"subscription:{subscription.pk}",
        payload={"subscription_id": str(subscription.pk), "days": days},
    )
    enqueue_reconcile(subscription.user_id, reason="subscription_extended")
    return True
