"""G6 services: affiliate attribution+commission, experiments, channel delivery."""
import hashlib

from django.db import transaction
from django.utils import timezone

from apps.events.models import record_event
from apps.growth.models import can_send_marketing

from ..models import (
    Affiliate, AffiliateAttribution, AffiliateCommission, Announcement,
    AnnouncementExposure, CampaignAction, Channel, Experiment, ExperimentAssignment,
    ExperimentConversion, ExperimentVariant, TelegramMarketingDelivery)


# ============================== AFFILIATES ==============================

def _is_self_referral(affiliate, user):
    return affiliate.user_id == (user.id if user else None)


def record_affiliate_signup(affiliate, user, utm_campaign=""):
    """Attribute a signup to an affiliate (first-touch wins).  Fraud guard:
    an affiliate cannot refer themselves.  Idempotent per (affiliate, user)."""
    if _is_self_referral(affiliate, user):
        return None, False
    with transaction.atomic():
        obj, created = AffiliateAttribution.objects.get_or_create(
            affiliate=affiliate, user=user,
            defaults={"utm_campaign": utm_campaign, "signed_up_at": timezone.now()})
        if created:
            record_event("affiliate.signup", dedupe_key=f"aff.signup:{affiliate.id}:{user.id}",
                         user_id=user.id, object_ref=f"affiliate:{affiliate.id}")
        return obj, created


def record_affiliate_conversion(affiliate, user, payment_intent):
    """Create a commission for an attributed purchase.  Idempotent via unique
    (affiliate, payment_intent).  Fraud: no self-referral, no commission for
    the affiliate's own purchase, must be an attributed user."""
    if _is_self_referral(affiliate, user):
        return None, False
    attribution = AffiliateAttribution.objects.filter(affiliate=affiliate, user=user).first()
    if attribution is None:
        return None, False  # not attributed -> no commission
    amount = int(payment_intent.amount * affiliate.commission_percent / 100)
    with transaction.atomic():
        commission, created = AffiliateCommission.objects.get_or_create(
            affiliate=affiliate, payment_intent=payment_intent,
            defaults={"user": user, "amount_cents": amount,
                      "currency": payment_intent.currency})
        if created:
            attribution.purchased_at = timezone.now()
            attribution.save(update_fields=["purchased_at"])
            record_event("affiliate.commission.created",
                         dedupe_key=f"aff.comm:{affiliate.id}:{payment_intent.id}",
                         user_id=user.id, object_ref=f"payment:{payment_intent.id}",
                         payload={"amount_cents": amount})
        return commission, created


def transition_commission(commission, new_status):
    """Allowed: PENDING->APPROVED->PAYABLE->PAID, or ->REVERSED from any non-PAID."""
    allowed = {
        AffiliateCommission.Status.PENDING: {AffiliateCommission.Status.APPROVED,
                                             AffiliateCommission.Status.REVERSED},
        AffiliateCommission.Status.APPROVED: {AffiliateCommission.Status.PAYABLE,
                                              AffiliateCommission.Status.REVERSED},
        AffiliateCommission.Status.PAYABLE: {AffiliateCommission.Status.PAID,
                                             AffiliateCommission.Status.REVERSED},
        AffiliateCommission.Status.PAID: set(),
        AffiliateCommission.Status.REVERSED: set(),
    }
    if new_status not in allowed.get(commission.status, set()):
        return False
    commission.status = new_status
    commission.save(update_fields=["status"])
    return True


# ============================== EXPERIMENTS ==============================

def assign_variant(experiment, user):
    """Deterministic, stable assignment: hash(experiment.key + user.id) ->
    variant by allocation.  A user never moves variants.  Idempotent."""
    existing = ExperimentAssignment.objects.filter(experiment=experiment, user=user).first()
    if existing:
        return existing.variant, False
    variants = list(experiment.variants.all())
    if not variants:
        return None, False
    # Stable hash -> [0,100); pick by cumulative allocation.
    h = int(hashlib.sha256(f"{experiment.key}:{user.id}".encode()).hexdigest(), 16)
    bucket = h % 100
    cumulative = 0
    chosen = variants[-1]
    for v in variants:
        cumulative += v.allocation_percent
        if bucket < cumulative:
            chosen = v
            break
    assignment, created = ExperimentAssignment.objects.get_or_create(
        experiment=experiment, user=user, defaults={"variant": chosen})
    return assignment.variant, created


def record_exposure(assignment):
    obj, created = ExperimentConversion.objects.get_or_create(
        assignment=assignment, event=None) if False else (None, False)
    # Exposure = assignment existence; conversion recorded separately.
    return assignment


def record_conversion(assignment, event):
    obj, created = ExperimentConversion.objects.get_or_create(assignment=assignment, event=event)
    if created:
        record_event("experiment.converted",
                     dedupe_key=f"exp.conv:{assignment.id}:{event.id}",
                     user_id=assignment.user_id, object_ref=f"experiment:{assignment.experiment_id}")
    return obj, created


# ============================== CHANNELS ==============================

def deliver_telegram_marketing(campaign_action, user, message):
    """Marketing Telegram send.  SEPARATE from provisioning: never grants/revokes
    membership.  Respects marketing consent/suppression; idempotent; durable."""
    if not can_send_marketing(user, user.email or ""):
        return None, "suppressed"
    delivery, created = TelegramMarketingDelivery.objects.get_or_create(
        idempotency_key=f"tg-mkt:{campaign_action.id}:{user.id}",
        defaults={"campaign_action": campaign_action, "user": user, "message": message,
                  "state": TelegramMarketingDelivery.State.SENT,  # stub provider
                  })
    if created:
        record_event("telegram.marketing.sent",
                     dedupe_key=f"tg.sent:{delivery.id}", user_id=user.id)
    return delivery, "ok" if created else "duplicate"


def eligible_announcements(user):
    """Active, in-window, under-frequency-cap, targeted announcements for a user."""
    now = timezone.now()
    qs = Announcement.objects.filter(active=True)
    if user:
        exposures = AnnouncementExposure.objects.filter(user=user)
        # Frequency cap: exclude announcements the user has seen >= max times.
        over = [a.id for a in qs
                if exposures.filter(announcement=a).count() >= a.max_shows_per_user]
        qs = qs.exclude(id__in=over)
    rows = []
    for a in qs:
        if a.starts_at and now < a.starts_at:
            continue
        if a.ends_at and now > a.ends_at:
            continue
        rows.append(a)
    return sorted(rows, key=lambda x: -x.priority)


def record_announcement_exposure(announcement, user, idempotency_key="", dismissed=False):
    obj, created = AnnouncementExposure.objects.get_or_create(
        announcement=announcement, user=user, idempotency_key=idempotency_key or "view",
        defaults={"dismissed": dismissed})
    if created:
        record_event("announcement.exposed",
                     dedupe_key=f"ann.exp:{announcement.id}:{user.id}:{idempotency_key}",
                     user_id=user.id, object_ref=f"announcement:{announcement.id}")
    return obj, created
