"""Transaction-specific refund eligibility (TRIAL vs STANDARD vs UPGRADE).

Design (per business rule): refund rules are a property of the applicable
plan/offer/POLICY VERSION, never hard-coded as always/never refundable.

  * CONFIG lives on evidence.PlanRefundTerms (live, staff-editable).
  * At checkout the applicable terms are FROZEN into
    CheckoutEvidence.pricing_snapshot and the refund/cancellation POLICY
    VERSIONS are FK'd onto the evidence row (immutable).
  * THIS ENGINE evaluates ONLY against the snapshot + the unified Refund
    ledger + timestamps. Changing PlanRefundTerms tomorrow can never alter
    the eligibility of a payment made yesterday.
  * The Refund ledger stays unified (Refund.commercial_context tags context).

Trial vs cancellation: refund eligibility and cancellation are different
events. CANCELLATION ends entitlement (CancellationRequest +
cancel_subscription); REFUND returns money (Refund row). A full refund still
cancels entitlement via the existing apply_refund_policy path - if the
business wants refund-WITH-access for trials, issue a partial refund
(entitlement unchanged by design).
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from .models import PaymentIntent, Refund
from .services import refund_context_for


class RefundNotEligible(Exception):
    """Advisory gate for manual refund actions (webhook refunds are provider
    facts and are always recorded - the ledger records reality; eligibility
    is evaluated for DECISIONS, not for recording provider events)."""


@dataclass
class Eligibility:
    context: str                      # trial | standard | upgrade
    is_trial: bool
    eligible: bool
    reason: str
    window_days: int | None
    window_end: timezone.datetime | None
    refund_policy_version: str
    cancellation_policy_version: str
    terms_snapshot: dict = field(default_factory=dict)
    already_refunded_cents: int = 0
    # True when eligibility CANNOT be determined (no checkout evidence);
    # decisions fall to a human. Prevents unfairly blocking legitimate
    # historical refunds while keeping the advisory gate meaningful.
    requires_manual_review: bool = False


def _snapshot(intent):
    try:
        return intent.checkout_evidence, intent.checkout_evidence.pricing_snapshot
    except Exception:
        return None, {}


def evaluate_refund_eligibility(intent, now=None) -> Eligibility:
    """Evaluate refund eligibility for THIS payment against the terms that
    were shown and accepted at its checkout."""
    now = now or timezone.now()
    ev, snap = _snapshot(intent)
    context = refund_context_for(intent)
    is_trial = snap.get("is_trial", context == Refund.CommercialContext.TRIAL)

    rp_ver = (f"{ev.refund_policy_version.policy_type} v"
              f"{ev.refund_policy_version.version}") if ev else "unknown (no evidence)"
    cp_ver = (f"{ev.cancellation_policy_version.policy_type} v"
              f"{ev.cancellation_policy_version.version}"
              ) if ev and ev.cancellation_policy_version else "folded into refund policy"

    refund_terms = snap.get("refund_terms") or {}
    window_days = (refund_terms.get("trial_refund_window_days")
                   if is_trial else None)
    if window_days is None:
        window_days = refund_terms.get("refund_window_days")
    already = intent.refunded_cents

    if not snap:
        return Eligibility(context, is_trial, eligible=False,
                           reason="no_checkout_evidence - manual review "
                                  "required (evaluate against the historical "
                                  "policy in force at purchase)",
                           window_days=None, window_end=None,
                           refund_policy_version=rp_ver,
                           cancellation_policy_version=cp_ver,
                           already_refunded_cents=already,
                           requires_manual_review=True)

    if window_days is None:
        return Eligibility(context, is_trial, eligible=False,
                           reason="no_refund_terms_configured",
                           window_days=None, window_end=None,
                           refund_policy_version=rp_ver,
                           cancellation_policy_version=cp_ver,
                           terms_snapshot=refund_terms,
                           already_refunded_cents=already)

    if window_days == 0:
        return Eligibility(context, is_trial, eligible=False,
                           reason="not_refundable_by_policy",
                           window_days=0, window_end=None,
                           refund_policy_version=rp_ver,
                           cancellation_policy_version=cp_ver,
                           terms_snapshot=refund_terms,
                           already_refunded_cents=already)

    charged_at = intent.created_at
    window_end = charged_at + timedelta(days=window_days)
    if now > window_end:
        return Eligibility(context, is_trial, eligible=False,
                           reason="refund_window_expired",
                           window_days=window_days, window_end=window_end,
                           refund_policy_version=rp_ver,
                           cancellation_policy_version=cp_ver,
                           terms_snapshot=refund_terms,
                           already_refunded_cents=already)
    if already >= intent.amount:
        return Eligibility(context, is_trial, eligible=False,
                           reason="already_fully_refunded",
                           window_days=window_days, window_end=window_end,
                           refund_policy_version=rp_ver,
                           cancellation_policy_version=cp_ver,
                           terms_snapshot=refund_terms,
                           already_refunded_cents=already)
    return Eligibility(context, is_trial, eligible=True,
                       reason="within_refund_window",
                       window_days=window_days, window_end=window_end,
                       refund_policy_version=rp_ver,
                       cancellation_policy_version=cp_ver,
                       terms_snapshot=refund_terms,
                       already_refunded_cents=already)


def assert_refundable(intent, now=None) -> Eligibility:
    """Advisory gate for MANUAL refunds (admin action). Webhook refunds are
    provider facts: recorded in the ledger unconditionally, eligibility is
    evaluated separately for the case narrative."""
    el = evaluate_refund_eligibility(intent, now=now)
    if not el.eligible:
        raise RefundNotEligible(f"{el.reason} (context={el.context}, "
                                f"policy={el.refund_policy_version})")
    return el


def evaluate_trial_cancellation_claim(intent, subscription=None,
                                      now=None) -> dict:
    """'I cancelled the trial before conversion' — verify against the
    CancellationRequest ledger (the decisive record) and the snapshotted
    cancellation deadline. Distinct from refund eligibility."""
    from apps.evidence.models import CancellationRequest
    now = now or timezone.now()
    _, snap = _snapshot(intent)
    deadline_hours = (snap.get("refund_terms") or {}).get(
        "cancellation_deadline_hours")
    reqs = list(CancellationRequest.objects.filter(
        subscription__user_id=intent.user_id).order_by("requested_at"))
    return {
        "cancellation_requests": [
            {"requested_at": r.requested_at.isoformat(),
             "channel": r.channel, "effective_at": r.effective_at.isoformat()}
            for r in reqs],
        "cancelled_before_conversion": bool(reqs),
        "cancellation_deadline_hours": deadline_hours,
        "snapshot": {"is_trial": snap.get("is_trial"),
                     "trial_duration_days": snap.get("trial_duration_days"),
                     "conversion_price_cents": snap.get("conversion_price_cents")},
    }
