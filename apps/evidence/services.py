"""Capture, backfill and confirmation services for the evidence layer."""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.policies.models import PolicyAcceptance, PolicyVersion
from apps.policies.services import (
    CHECKOUT_POLICY_TYPES, record_acceptance, record_checkout_acceptances)

from .fingerprint import (
    FINGERPRINT_VERSION, collect_from_request, compute_fingerprint,
    compute_risk_device_id)
from .models import CheckoutEvidence, MembershipConfirmation, PaymentAuthentication

logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR") or "0.0.0.0")


def _pricing_snapshot(intent) -> dict:
    """Point-in-time commercial agreement copied from the immutable snapshot
    (PaymentIntent) plus the plan's description-as-shown.

    TRIAL CHECKOUTS additionally freeze: trial duration, conversion price,
    conversion disclosure, refund eligibility window, cancellation deadline
    and the refund terms text - so a trial dispute always shows the terms
    that applied to THAT purchase (never today's configuration).
    """
    plan = intent.plan
    snap = {
        "plan_id": str(plan.id),
        "plan_name": plan.name,
        "plan_description_shown": (plan.description or "")[:2000],
        "interval": getattr(intent.plan_price, "interval", ""),
        "price_cents": intent.amount,
        "base_amount_cents": intent.base_amount_cents,
        "currency": intent.currency,
        "country": intent.country,
        "is_trial": bool(getattr(plan, "is_trial", False)),
        "is_upgrade": bool(getattr(intent, "is_upgrade", False)),
        "coupon_code": intent.applied_coupon_code or "",
        "coupon_discount_cents": intent.coupon_discount_cents,
        "has_referral_discount": intent.applied_referral_discount_id is not None,
    }
    # Freeze the refund/cancellation terms that apply to THIS transaction.
    terms = getattr(plan, "refund_terms", None)
    refund_terms = {
        "refund_window_days": getattr(terms, "refund_window_days", 0),
        "trial_refund_window_days": getattr(terms,
                                            "trial_refund_window_days", None),
        "cancellation_deadline_hours": getattr(terms,
                                               "cancellation_deadline_hours",
                                               None),
        "refund_terms_text": (getattr(terms, "refund_terms_text", "") or ""
                              )[:2000],
    }
    snap["refund_terms"] = refund_terms
    if snap["is_trial"]:
        snap["trial_duration_days"] = getattr(plan, "trial_duration_days", None)
        # Conversion price: the plan's own price for the billing interval
        # (NOT base_amount, which reflects coupons/discounts on the trial
        # fee). Falls back to base_amount only if no price row exists.
        conv = None
        try:
            from apps.subscriptions.models import PlanPrice
            conv = PlanPrice.objects.filter(
                plan=plan,
                interval=getattr(intent.plan_price, "interval", "monthly"),
                is_active=True).values_list("price_cents", flat=True).first()
        except Exception:
            conv = None
        snap["conversion_price_cents"] = conv or intent.base_amount_cents or None
        snap["conversion_interval"] = snap["interval"]
        snap["initial_charge_cents"] = intent.amount
        # Plain-language conversion disclosure, also shown at checkout.
        days = snap["trial_duration_days"] or 7
        snap["conversion_disclosure"] = (
            f"${intent.amount / 100:.0f} for the first {days} days, then "
            f"${(intent.base_amount_cents or 0) / 100:.0f}/"
            f"{snap['interval']} unless cancelled before the trial ends.")
    return snap


@transaction.atomic
def capture_checkout_evidence(intent, request, client_signals=None,
                              client_ts=None) -> CheckoutEvidence:
    """Write-once capture of the agreement moment, 1:1 with the PaymentIntent.

    Call exactly once per intent, BEFORE provider handoff. Records the policy
    acceptances (terms / refund / risk / cancellation) in the same transaction
    so the agreement moment is atomic.

    Raises nothing on concurrent duplicate calls: the intent row is locked
    (select_for_update) so double-submits serialize; the first write wins
    and the second returns the existing row (idempotent, no IntegrityError).
    Raises RuntimeError only if a required policy version is not ACTIVE
    (never fabricate).
    """
    from apps.payments.models import PaymentIntent
    locked = PaymentIntent.objects.select_for_update().get(pk=intent.pk)
    existing = CheckoutEvidence.objects.filter(payment_intent=locked).first()
    if existing is not None:
        return existing
    intent = locked

    # ONE customer checkbox -> THREE granular backend records. Never fabricate.
    versions = {pt: PolicyVersion.active(pt) for pt in CHECKOUT_POLICY_TYPES}
    missing = [pt for pt, v in versions.items() if v is None]
    if missing:
        raise RuntimeError(
            f"No ACTIVE policy versions for {missing}; refusing to capture "
            "checkout evidence without the agreement record.")
    # Optional separate Cancellation Policy, only if one is ACTIVE:
    versions[PolicyVersion.PolicyType.CANCELLATION] = PolicyVersion.active(
        PolicyVersion.PolicyType.CANCELLATION)

    signals = client_signals or collect_from_request(request)
    fp_hex, canonical = compute_fingerprint(signals)
    evidence = CheckoutEvidence.objects.create(
        payment_intent=intent,
        ip_address=_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:3000],
        device_fingerprint=fp_hex,
        fingerprint_version=FINGERPRINT_VERSION,
        device_signals=canonical,
        risk_device_id=compute_risk_device_id(fp_hex),
        accept_language=request.META.get("HTTP_ACCEPT_LANGUAGE", "")[:64],
        session_ref=CheckoutEvidence.session_ref_for(
            request.session.session_key),
        pricing_snapshot=_pricing_snapshot(intent),
        terms_version=versions[PolicyVersion.PolicyType.TERMS],
        refund_policy_version=versions[PolicyVersion.PolicyType.REFUND],
        risk_disclaimer_version=versions[PolicyVersion.PolicyType.RISK],
        cancellation_policy_version=versions.get(PolicyVersion.PolicyType.CANCELLATION),
        accepted_at=timezone.now(),
        client_ts=client_ts,
        checkout_version="1",
    )
    # ONE checkbox -> granular, individually-versioned acceptance records,
    # all tied to the user, this payment attempt, timestamp, IP, UA, device
    # evidence (via checkout_evidence) and session reference:
    record_checkout_acceptances(request.user, request,
                                checkout_evidence=evidence)
    if versions.get(PolicyVersion.PolicyType.CANCELLATION) is not None:
        record_acceptance(
            request.user, versions[PolicyVersion.PolicyType.CANCELLATION],
            request, context=PolicyAcceptance.Context.CHECKOUT,
            checkout_evidence=evidence)
    return evidence


def backfill_payment_authentication(intent, provider_api=None) -> PaymentAuthentication | None:
    """Fetch the provider's authentication result once, write-once.

    Stripe: session -> payment_intent -> latest charge -> payment_method_details.
    Razorpay: order -> payment entity -> method/card fields (often sparse -
    blank is a valid, honest result; never invent values).
    Returns None when nothing is retrievable.
    """
    if PaymentAuthentication.objects.filter(payment_intent=intent).exists():
        return intent.authentication
    if provider_api is None:
        from apps.payments import providers as provider_api

    data = None
    if intent.provider == "stripe":
        data = provider_api.fetch_stripe_authentication(intent)
    elif intent.provider == "razorpay":
        data = provider_api.fetch_razorpay_authentication(intent)
    if not data:
        return None
    return PaymentAuthentication.objects.create(payment_intent=intent, **data)


@transaction.atomic
def confirm_membership_from_snapshot(snapshot) -> MembershipConfirmation | None:
    """Create/update a MembershipConfirmation from an OBSERVED snapshot row.

    Only ever called with observed data (ChannelMembershipSnapshot). Never
    infers membership from grants or invites.
    """
    if not getattr(snapshot, "is_member", False):
        return None
    obj, created = MembershipConfirmation.objects.get_or_create(
        user=snapshot.user,
        platform=snapshot.platform,
        external_id=snapshot.external_id,
        defaults={
            "confirmed_at": snapshot.checked_at or timezone.now(),
            "assignment": _assignment_for(snapshot),
            "source_snapshot": snapshot,
        })
    if not created:
        MembershipConfirmation.objects.filter(pk=obj.pk).update(
            last_seen_at=snapshot.checked_at or timezone.now())
        obj.refresh_from_db()
    return obj


def _assignment_for(snapshot):
    from apps.bot_integration.models import UserChannelAssignment
    return UserChannelAssignment.objects.filter(
        user=snapshot.user, platform=snapshot.platform,
        external_id=snapshot.external_id).first()
