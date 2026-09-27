"""Dispute ingestion, state machine and evidence services."""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.evidence.models import (
    CancellationRequest, CheckoutEvidence, MembershipConfirmation,
    PaymentAuthentication)
from apps.payments.models import PaymentIntent

from .models import Dispute, DisputeEvent, DisputeEvidencePackage

logger = logging.getLogger(__name__)

_REASON_MAP = {
    "fraudulent": Dispute.ReasonCategory.FRAUD,
    "unrecognized": Dispute.ReasonCategory.UNRECOGNIZED,
    "product_not_received": Dispute.ReasonCategory.NOT_RECEIVED,
    "product_unacceptable": Dispute.ReasonCategory.NOT_AS_DESCRIBED,
    "subscription_canceled": Dispute.ReasonCategory.RECURRING_CANCELLED,
    "credit_not_processed": Dispute.ReasonCategory.CREDIT_NOT_PROCESSED,
    "duplicate": Dispute.ReasonCategory.DUPLICATE,
    "customer_initiated": Dispute.ReasonCategory.CUSTOMER_INITIATED,
}


def _log(dispute, event_type, actor=None, from_status="", to_status="",
         note="", occurred_at=None):
    return DisputeEvent.objects.create(
        dispute=dispute, event_type=event_type, actor=actor,
        from_status=from_status, to_status=to_status, note=note,
        occurred_at=occurred_at or timezone.now())


@transaction.atomic
def ingest_dispute_event(provider, dispute_payload, intent, event_type):
    """Idempotent webhook ingestion (Stripe dispute lifecycle).

    dispute.created/.funds_withdrawn/.closed - plus Razorpay manual
    registration via register_manual_dispute().
    """
    dispute_id = dispute_payload.get("id") or ""
    if not dispute_id:
        return None
    dispute, created = Dispute.objects.get_or_create(
        provider=provider, provider_dispute_id=dispute_id,
        defaults={
            "payment_intent": intent,
            "reason_raw": dispute_payload.get("reason") or "",
            "reason_category": _REASON_MAP.get(
                dispute_payload.get("reason") or "", Dispute.ReasonCategory.OTHER),
            "amount_cents": dispute_payload.get("amount") or 0,
            "currency": (dispute_payload.get("currency") or "usd").upper(),
            "evidence_due_at": _ts(dispute_payload.get("evidence_details", {})
                                   .get("due_by")),
        })
    if created:
        _log(dispute, DisputeEvent.EventType.CREATED,
             note=f"Webhook {event_type}; reason={dispute.reason_raw}")
        # NO early return: a charge.dispute.closed event may be the FIRST
        # one we ever see for this case (out-of-order/replay delivery).
        # The lifecycle branches below must still run.

    # Lifecycle updates:
    if event_type == "charge.dispute.funds_withdrawn":
        if dispute.funds_withdrawn_at is None:
            Dispute.objects.filter(pk=dispute.pk).update(
                funds_withdrawn_at=timezone.now())
            dispute.refresh_from_db()
            _log(dispute, DisputeEvent.EventType.STATUS_CHANGED,
                 note="Funds withdrawn by issuer (NOT final; entitlement "
                      "intentionally unchanged until resolved)")
    elif event_type == "charge.dispute.updated":
        updates = {}
        due = _ts((dispute_payload.get("evidence_details") or {}).get("due_by"))
        if due and due != dispute.evidence_due_at:
            updates["evidence_due_at"] = due
        reason = dispute_payload.get("reason") or ""
        if reason and reason != dispute.reason_raw:
            updates["reason_raw"] = reason
            updates["reason_category"] = _REASON_MAP.get(
                reason, Dispute.ReasonCategory.OTHER)
        if updates:
            Dispute.objects.filter(pk=dispute.pk).update(**updates)
            dispute.refresh_from_db()
            _log(dispute, DisputeEvent.EventType.STATUS_CHANGED,
                 note=f"Updated: {sorted(updates)}")
    elif event_type == "charge.dispute.closed":
        _close_from_payload(dispute, dispute_payload)
    return dispute


@transaction.atomic
def register_manual_dispute(intent, provider_dispute_id, reason_raw="",
                            amount_cents=None, currency="USD",
                            opened_at=None, actor=None, note=""):
    """Razorpay (or other) manual case registration - same code path."""
    from django.db import IntegrityError
    try:
        dispute = Dispute.objects.create(
            provider=intent.provider, provider_dispute_id=provider_dispute_id,
            payment_intent=intent, reason_raw=reason_raw,
            reason_category=_REASON_MAP.get(reason_raw, Dispute.ReasonCategory.OTHER),
            amount_cents=amount_cents if amount_cents is not None else intent.amount,
            currency=currency, opened_at=opened_at or timezone.now())
    except IntegrityError:
        return Dispute.objects.get(provider=intent.provider,
                                   provider_dispute_id=provider_dispute_id)
    _log(dispute, DisputeEvent.EventType.CREATED, actor=actor,
         note=f"Manual registration: {note}")
    return dispute


def _ts(value):
    from django.utils.dateparse import parse_datetime
    if isinstance(value, (int, float)):
        from datetime import datetime as dt
        return dt.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        return parse_datetime(value)
    return None


def _close_from_payload(dispute, payload):
    status = (payload.get("status") or "").lower()
    won = status in ("won", "warning_closed")
    lost = status in ("lost", "warning_issued")
    with transaction.atomic():
        d = Dispute.objects.select_for_update().get(pk=dispute.pk)
        if d.status in Dispute.TERMINAL:
            return
        if won:
            _transition(d, Dispute.Status.WON, note="Issuer ruled for merchant")
        elif lost:
            _transition(d, Dispute.Status.LOST,
                        note="Issuer ruled for cardholder")
            _action_access(d)
        else:
            _log(d, DisputeEvent.EventType.NOTE,
                 note=f"Dispute closed with status={status!r}")


def _transition(dispute, to_status, actor=None, note=""):
    frm = dispute.status
    Dispute.objects.filter(pk=dispute.pk).update(
        status=to_status, resolved_at=timezone.now())
    dispute.status = to_status
    dispute.resolved_at = timezone.now()
    _log(dispute, DisputeEvent.EventType.STATUS_CHANGED, actor=actor,
         from_status=frm, to_status=to_status, note=note)


def _action_access(dispute):
    """Confirmed loss -> exactly-once revocation via THE ONE existing path:
    payments.services.confirm_chargeback (atomic claim on the PaymentIntent).
    Whichever layer runs first performs it; the other sees performed=False.
    Never runs on opened disputes or on funds_withdrawn."""
    if dispute.access_actioned:
        return
    from apps.payments.services import confirm_chargeback
    performed = confirm_chargeback(
        dispute.payment_intent,
        dispute_reference=dispute.provider_dispute_id,
        source="dispute-service")
    Dispute.objects.filter(pk=dispute.pk).update(access_actioned=True)
    dispute.access_actioned = True
    _log(dispute, DisputeEvent.EventType.ACCESS_ACTION,
         note=f"confirm_chargeback performed={performed} (exactly once)")
    if performed:
        try:
            from apps.payments.notifications import notify_chargedback
            notify_chargedback(dispute.payment_intent)
        except Exception:
            logger.exception("chargedback notification failed")


@transaction.atomic
def resolve_manually(dispute, to_status, actor, note=""):
    if to_status not in Dispute.TERMINAL:
        raise ValueError("resolve_manually requires a terminal status")
    if dispute.status in Dispute.TERMINAL:
        raise ValueError(f"already terminal: {dispute.status}")
    _transition(dispute, to_status, actor=actor, note=note or "Manual resolve")
    if to_status == Dispute.Status.WON:
        from apps.payments.services import record_dispute_won
        record_dispute_won(dispute.payment_intent,
                           dispute_reference=dispute.provider_dispute_id,
                           source="manual", actor=actor)
    if to_status in (Dispute.Status.LOST, Dispute.Status.ACCEPTED):
        _action_access(dispute)


# ── Agreement sentence (the "Terms accepted" evidence) ────────────────────
def agreement_sentence(intent) -> dict:
    """Renders the exact, per-transaction acceptance statement:

    'Customer accepted Terms of Service vX.X, Refund & Cancellation Policy
    vX.X, and Risk Disclosure vX.X at [timestamp] for this specific
    transaction.'  Every fact links to an immutable historical version.
    """
    try:
        ev = intent.checkout_evidence
    except CheckoutEvidence.DoesNotExist:
        return {"available": False,
                "reason": "No checkout evidence captured for this transaction "
                          "(pre-instrumentation). Never fabricated."}
    parts, links = [], []
    for label, pv in (
        ("Terms of Service", ev.terms_version),
        ("Refund & Cancellation Policy", ev.refund_policy_version),
        ("Risk Disclosure", ev.risk_disclaimer_version),
    ):
        parts.append(f"{label} v{pv.version}")
        links.append({"label": label, "version": pv.version,
                      "policy_type": pv.policy_type,
                      "content_sha256": pv.content_sha256,
                      "url": f"/policies/{pv.policy_type}/v/{pv.version}/"})
    sentence = (f"Customer accepted {parts[0]}, {parts[1]}, and {parts[2]} "
                f"at {ev.accepted_at:%Y-%m-%d %H:%M:%S %Z} for this specific "
                f"transaction.")
    return {"available": True, "sentence": sentence, "links": links,
            "accepted_at": ev.accepted_at, "ip": ev.ip_address,
            "device_fingerprint": ev.device_fingerprint,
            "pricing_snapshot": ev.pricing_snapshot,
            "evidence_id": str(ev.id)}


# ── Unified timeline (reads tables; infers nothing) ───────────────────────
def build_timeline(dispute) -> list:
    intent = dispute.payment_intent
    user = intent.user
    items = []

    def add(ts, source, title, detail="", ref=""):
        if ts is not None:
            items.append({"ts": ts.isoformat(), "source": source,
                          "title": title, "detail": detail, "ref": ref})

    add(intent.created_at, "payments", "Payment initiated",
        f"{intent.amount} {intent.currency} via {intent.provider}",
        f"payments.PaymentIntent:{intent.pk}")
    try:
        ev = intent.checkout_evidence
        add(ev.accepted_at, "evidence", "Checkout consent captured",
            agreement_sentence(intent)["sentence"],
            f"evidence.CheckoutEvidence:{ev.pk}")
    except CheckoutEvidence.DoesNotExist:
        pass
    if intent.status == PaymentIntent.Status.SUCCESS:
        add(intent.updated_at, "payments", "Payment succeeded",
            f"Provider ref {intent.provider_reference}",
            f"payments.PaymentIntent:{intent.pk}")
    for r in _refunds(intent):
        add(r.refunded_at, "payments", "Refund issued",
            f"{r.amount_cents} {r.currency} (ref {r.provider_refund_id})",
            f"payments.Refund:{r.pk}")
    from apps.subscriptions.models import SubscriptionHistory
    for sh in SubscriptionHistory.objects.filter(
            user=user).order_by("created_at"):
        add(sh.created_at, "subscription",
            sh.get_event_type_display(),
            str(sh.notes or "")[:140],
            f"subscriptions.SubscriptionHistory:{sh.pk}")
    try:
        from apps.events.models import Event
        for ev in Event.objects.filter(user_id=user.id).order_by(
                "occurred_at")[:100]:
            add(ev.occurred_at, "events", ev.event_type.replace(".", " "),
                "", f"events.Event:{ev.pk}")
    except Exception:
        pass
    for cr in CancellationRequest.objects.filter(
            subscription__user=user).order_by("requested_at"):
        add(cr.requested_at, "evidence", "Cancellation requested",
            f"Channel: {cr.get_channel_display()}; effective {cr.effective_at}",
            f"evidence.CancellationRequest:{cr.pk}")
    for mc in MembershipConfirmation.objects.filter(user=user).order_by(
            "confirmed_at"):
        add(mc.confirmed_at, "fulfillment", "Membership confirmed",
            f"{mc.platform}:{mc.external_id}", "")
    for dlv in _deliveries(user)[:50]:
        ts = getattr(dlv, "sent_at", None) or getattr(dlv, "created_at", None)
        tmpl = ""
        try:
            tmpl = dlv.template_version.template.name
        except Exception:
            tmpl = "email"
        state = getattr(dlv, "state", "") or getattr(dlv, "status", "")
        add(ts, "comms", f"Email: {tmpl}", state, f"emailing.Delivery:{dlv.pk}")
    for se in user.security_events.all()[:100]:
        add(se.occurred_at, "security", se.get_event_type_display(),
            se.user_agent[:80], f"accounts.SecurityEvent:{se.pk}")
    for pkg in dispute.packages.all():
        add(pkg.generated_at, "disputes", "Evidence package generated",
            f"v{pkg.generator_version} sha256:{pkg.content_sha256[:12]}…",
            f"disputes.DisputeEvidencePackage:{pkg.pk}")
    for e in dispute.events.all():
        add(e.occurred_at, "disputes",
            e.get_event_type_display(), e.note[:140], "")
    items.sort(key=lambda x: x["ts"])
    return items


# ── CE3.0 matcher ─────────────────────────────────────────────────────────
def ce3_matches(dispute) -> list:
    """Prior undisputed transactions on the same payment credential
    (card_fingerprint), 120-365 days before the dispute, with matching
    device/login elements. Returns qualifying payment ids + matched elements.
    """
    try:
        auth = dispute.payment_intent.authentication
        card_fp = auth.card_fingerprint
    except PaymentAuthentication.DoesNotExist:
        return []
    if not card_fp:
        return []
    try:
        ev = dispute.payment_intent.checkout_evidence
        dev_fp, ip, session_ref = (ev.device_fingerprint, ev.ip_address,
                                   ev.session_ref)
    except CheckoutEvidence.DoesNotExist:
        dev_fp, ip, session_ref = "", "", ""
    window_end = dispute.opened_at
    window_start = window_end - timedelta(days=365)
    min_gap = timedelta(days=119)
    out = []
    for auth2 in PaymentAuthentication.objects.filter(
            card_fingerprint=card_fp).select_related(
            "payment_intent__user").order_by("payment_intent__created_at"):
        intent2 = auth2.payment_intent
        if intent2.pk == dispute.payment_intent_id:
            continue
        if intent2.status != PaymentIntent.Status.SUCCESS:
            continue
        if intent2.user_id != dispute.payment_intent.user_id:
            continue  # CE3: same credential AND same account context
        if not (window_start <= intent2.created_at <= window_end):
            continue
        if window_end - intent2.created_at <= min_gap:
            continue
        # "Undisputed" = no open/submitted case AND no confirmed chargeback.
        # WON/WITHDRAWN outcomes qualify as undisputed history.
        if Dispute.objects.filter(
                payment_intent=intent2).exclude(
                status__in=[Dispute.Status.WON,
                            Dispute.Status.WITHDRAWN]).exists():
            continue
        if getattr(intent2, "chargeback_confirmed", False):
            continue  # pre-Dispute-era confirmed chargeback
        elements = []
        try:
            ev2 = intent2.checkout_evidence
            if dev_fp and ev2.device_fingerprint == dev_fp:
                elements.append("device_fingerprint")
            if ip and ev2.ip_address == ip:
                elements.append("ip_address")
            if session_ref and ev2.session_ref == session_ref:
                elements.append("session_ref")
        except CheckoutEvidence.DoesNotExist:
            pass
        elements.append("account_id")
        if len(elements) >= 2:
            out.append({"payment_intent_id": str(intent2.pk),
                        "created_at": intent2.created_at.isoformat(),
                        "amount": intent2.amount, "currency": intent2.currency,
                        "matched_elements": elements})
    return out


# ── Readiness ─────────────────────────────────────────────────────────────
def evaluate_readiness(dispute) -> dict:
    intent = dispute.payment_intent
    items = []

    def item(key, label, ok, why, critical=False):
        items.append({"key": key, "label": label,
                      "status": "PRESENT" if ok else "MISSING",
                      "why": why, "critical": critical})

    cat = dispute.reason_category
    try:
        auth = intent.authentication
        tds_ok = auth.three_ds_result in (
            "authenticated", "liability_shift")
        item("3ds", "3DS authentication", tds_ok,
             "Liability shift for CNP fraud; strongest single item.",
             critical=cat in (Dispute.ReasonCategory.FRAUD,
                              Dispute.ReasonCategory.UNRECOGNIZED))
        item("avs_cvc", "AVS / CVC checks",
             bool(auth.cvc_check or auth.avs_zip_check),
             "Credential-knowledge evidence (supporting).")
        item("card_fp", "Card fingerprint", bool(auth.card_fingerprint),
             "Links same credential across payments (CE3.0 substrate).")
    except PaymentAuthentication.DoesNotExist:
        item("3ds", "3DS authentication", False,
             "No provider auth data; backfill if retrievable.", critical=True)
    snap, is_trial = {}, False
    try:
        ev = intent.checkout_evidence
        snap = ev.pricing_snapshot or {}
        is_trial = bool(snap.get("is_trial"))
        item("checkout_evidence", "Checkout evidence (IP/device/terms)",
             True, "Captured at the agreement moment.")
        item("terms", "Terms acceptance", True,
             "Exact historical versions recorded.")
        if cat == Dispute.ReasonCategory.RECURRING_CANCELLED:
            has_cancel = CancellationRequest.objects.filter(
                subscription__user=intent.user).exists()
            item("cancel_record", "Cancellation request record", has_cancel,
                 "Decisive: requested_at vs charge date.", critical=True)
    except CheckoutEvidence.DoesNotExist:
        item("checkout_evidence", "Checkout evidence (IP/device/terms)",
             False, "Not captured for this transaction (pre-instrumentation).",
             critical=True)
    if cat in (Dispute.ReasonCategory.FRAUD,
               Dispute.ReasonCategory.UNRECOGNIZED):
        item("ce3", "CE3.0 qualifying history", bool(ce3_matches(dispute)),
             "2+ prior undisputed txns, same credential, matching elements.",
             critical=False)
    if is_trial:
        item("trial_snapshot", "Trial checkout snapshot (duration / "
             "conversion / disclosure)", bool(snap.get("trial_duration_days")),
             "Trial disputes stand on the exact conversion disclosure shown.",
             critical=False)
        item("trial_cancellation", "Trial cancellation record",
             CancellationRequest.objects.filter(
                 subscription__user=intent.user).exists(),
             "'Cancelled before conversion' claims are decided by this ledger.",
             critical=cat == Dispute.ReasonCategory.RECURRING_CANCELLED)
    if cat == Dispute.ReasonCategory.NOT_RECEIVED:
        confirmed = MembershipConfirmation.objects.filter(
            user=intent.user).exists()
        item("membership", "Membership confirmation", confirmed,
             "Observed community join (not inferred).", critical=True)
    present = sum(1 for i in items if i["status"] == "PRESENT")
    missing_critical = [i for i in items
                        if i["critical"] and i["status"] == "MISSING"]
    state = ("MISSING" if missing_critical
             else "COMPLETE" if all(i["status"] == "PRESENT" for i in items)
             else "PARTIAL")
    return {"state": state, "present": present, "total": len(items),
            "missing_critical": [i["key"] for i in missing_critical],
            "items": items}


# ── related-name guards (codebase drift protection) ───────────────────────
def _refunds(intent):
    mgr = getattr(intent, "refunds", None)
    if mgr is not None:
        return mgr.all()
    from apps.payments.models import Refund
    return Refund.objects.filter(payment_intent=intent)


def _deliveries(user):
    from apps.emailing.models import Delivery
    return Delivery.objects.filter(user=user)


# ── Evidence package generation + narrative drafting ───────────────────────
GENERATOR_VERSION = "1"

# The agreement section is included when relevant to the dispute reason;
# the IMMUTABLE historical version content is always what is referenced.
TERMS_RELEVANT = {
    Dispute.ReasonCategory.NOT_AS_DESCRIBED,
    Dispute.ReasonCategory.RECURRING_CANCELLED,
    Dispute.ReasonCategory.NOT_RECEIVED,
    Dispute.ReasonCategory.FRAUD,
    Dispute.ReasonCategory.UNRECOGNIZED,
    Dispute.ReasonCategory.OTHER,
}


def _fact(label, value, source):
    return {"label": label, "value": str(value or "—"), "source": source}


def build_sections(dispute) -> dict:
    intent = dispute.payment_intent
    user = intent.user
    sections = {}
    # PLAN TYPE block: TRIAL vs PAID, with the exact per-transaction terms.
    try:
        ev0 = intent.checkout_evidence
        snap = ev0.pricing_snapshot
    except CheckoutEvidence.DoesNotExist:
        ev0, snap = None, {}
    is_trial = bool(snap.get("is_trial"))
    plan_type_facts = [
        _fact("Plan type", "TRIAL" if is_trial else "PAID",
              f"evidence.CheckoutEvidence:{ev0.pk}" if ev0 else ""),
        _fact("Price charged", f"{intent.amount} {intent.currency}",
              f"payments.PaymentIntent:{intent.pk}"),
    ]
    if is_trial:
        plan_type_facts += [
            _fact("Trial duration", f"{snap.get('trial_duration_days')} days",
                  f"evidence.CheckoutEvidence:{ev0.pk}"),
            _fact("Initial charge", f"{snap.get('initial_charge_cents')} "
                  f"{intent.currency}",
                  f"evidence.CheckoutEvidence:{ev0.pk}"),
            _fact("Conversion price",
                  f"{snap.get('conversion_price_cents')} {intent.currency} / "
                  f"{snap.get('conversion_interval')}",
                  f"evidence.CheckoutEvidence:{ev0.pk}"),
            _fact("Conversion disclosure", snap.get("conversion_disclosure"),
                  f"evidence.CheckoutEvidence:{ev0.pk}"),
        ]
    rt = snap.get("refund_terms") or {}
    plan_type_facts += [
        _fact("Refund eligibility",
              f"window {rt.get('trial_refund_window_days') if is_trial else rt.get('refund_window_days')} "
              f"day(s)" + (" (trial window)" if is_trial and
                           rt.get("trial_refund_window_days") is not None else ""),
              f"evidence.CheckoutEvidence:{ev0.pk}" if ev0 else ""),
        _fact("Cancellation deadline",
              (f"{rt.get('cancellation_deadline_hours')}h before trial end"
               if rt.get("cancellation_deadline_hours") else "n/a"),
              f"evidence.CheckoutEvidence:{ev0.pk}" if ev0 else ""),
        _fact("Refund policy version",
              (f"{ev0.refund_policy_version.policy_type} v"
               f"{ev0.refund_policy_version.version}") if ev0 else "unknown",
              "policies.PolicyVersion"),
        _fact("Cancellation policy version",
              (f"{ev0.cancellation_policy_version.policy_type} v"
               f"{ev0.cancellation_policy_version.version}"
               ) if ev0 and ev0.cancellation_policy_version
              else "folded into refund policy",
              "policies.PolicyVersion"),
        _fact("Terms version",
              (f"{ev0.terms_version.policy_type} v{ev0.terms_version.version}"
               ) if ev0 else "unknown",
              "policies.PolicyVersion"),
        _fact("Risk disclosure version",
              (f"{ev0.risk_disclaimer_version.policy_type} v"
               f"{ev0.risk_disclaimer_version.version}") if ev0 else "unknown",
              "policies.PolicyVersion"),
    ]
    sections["plan_type"] = {"title": "Plan type & applicable terms",
                             "facts": plan_type_facts}

    # Live refund-eligibility evaluation (against the snapshot, not config).
    try:
        from apps.payments.refund_engine import evaluate_refund_eligibility
        el = evaluate_refund_eligibility(intent)
        sections["refund_eligibility"] = {
            "title": "Refund eligibility (as of this dispute)",
            "facts": [
                _fact("Eligible", str(el.eligible), "refund_engine"),
                _fact("Reason", el.reason, "refund_engine"),
                _fact("Context", el.context, "refund_engine"),
                _fact("Policy", el.refund_policy_version, "refund_engine"),
                _fact("Already refunded", f"{el.already_refunded_cents} "
                      f"{intent.currency}", "payments.Refund"),
            ],
        }
    except Exception:
        pass  # engine must never break package generation

    sections["transaction"] = {
        "title": "Transaction",
        "facts": [
            _fact("Payment intent", intent.pk,
                  f"payments.PaymentIntent:{intent.pk}"),
            _fact("Provider / reference",
                  f"{intent.provider} / {intent.provider_reference}",
                  f"payments.PaymentIntent:{intent.pk}"),
            _fact("Amount", f"{intent.amount} {intent.currency}",
                  f"payments.PaymentIntent:{intent.pk}"),
            _fact("Base amount (pre-coupon)",
                  f"{intent.base_amount_cents} {intent.currency}",
                  f"payments.PaymentIntent:{intent.pk}"),
            _fact("Coupon / referral discount",
                  f"{intent.applied_coupon_code or '-'} / "
                  f"{'yes' if intent.applied_referral_discount_id else 'no'}",
                  f"payments.PaymentIntent:{intent.pk}"),
            _fact("Country", intent.country,
                  f"payments.PaymentIntent:{intent.pk}"),
            _fact("Plan", intent.plan.name,
                  f"subscriptions.Plan:{intent.plan_id}"),
        ],
    }
    auth_sec = {"title": "Payment authentication", "facts": []}
    try:
        a = intent.authentication
        auth_sec["facts"] += [
            _fact("3DS result", a.get_three_ds_result_display(),
                  f"evidence.PaymentAuthentication:{a.pk}"),
            _fact("ECI", a.eci or "not exposed by PSP",
                  f"evidence.PaymentAuthentication:{a.pk}"),
            _fact("CVC check", a.cvc_check or "not reported",
                  f"evidence.PaymentAuthentication:{a.pk}"),
            _fact("AVS line1 / zip",
                  f"{a.avs_line1_check or 'n/r'} / {a.avs_zip_check or 'n/r'}",
                  f"evidence.PaymentAuthentication:{a.pk}"),
            _fact("Card", f"{a.card_brand} •••• {a.card_last4} "
                  f"({a.card_country})",
                  f"evidence.PaymentAuthentication:{a.pk}"),
            _fact("Wallet", a.wallet_type or "none",
                  f"evidence.PaymentAuthentication:{a.pk}"),
            _fact("Network txn id", a.network_txn_id or "n/r",
                  f"evidence.PaymentAuthentication:{a.pk}"),
        ]
        auth_sec["status"] = "present"
    except PaymentAuthentication.DoesNotExist:
        auth_sec["facts"] = [_fact("Authentication data",
                                   "Not available (never fabricated)", "")]
        auth_sec["status"] = "missing"
    sections["authentication"] = auth_sec

    # Agreement: exact historical versions; only when relevant to the reason.
    if dispute.reason_category in TERMS_RELEVANT:
        agr = agreement_sentence(intent)
        tsec = {"title": "Agreement (Terms / Refund & Cancellation / Risk "
                        "Disclosure)",
                "status": "present" if agr["available"] else "missing",
                "facts": []}
        if agr["available"]:
            tsec["facts"].append(_fact("Acceptance statement", agr["sentence"],
                                       f"evidence.CheckoutEvidence:{agr['evidence_id']}"))
            for link in agr["links"]:
                tsec["facts"].append(_fact(
                    link["label"],
                    f"v{link['version']} - sha256 "
                    f"{link['content_sha256'][:16]}... - {link['url']}",
                    "policies.PolicyVersion"))
            tsec["facts"] += [
                _fact("Acceptance IP", agr["ip"],
                      f"evidence.CheckoutEvidence:{agr['evidence_id']}"),
                _fact("Device fingerprint",
                      agr["device_fingerprint"][:16] + "...",
                      f"evidence.CheckoutEvidence:{agr['evidence_id']}"),
                _fact("Offer shown",
                      f"{agr['pricing_snapshot'].get('plan_name')} - "
                      f"{agr['pricing_snapshot'].get('price_cents')} "
                      f"{agr['pricing_snapshot'].get('currency')} / "
                      f"{agr['pricing_snapshot'].get('interval')}",
                      f"evidence.CheckoutEvidence:{agr['evidence_id']}"),
            ]
        else:
            tsec["facts"].append(_fact("Acceptance statement", agr["reason"],
                                       ""))
        sections["agreement"] = tsec

    ful = {"title": "Fulfillment (granted / confirmed)", "facts": []}
    for mc in MembershipConfirmation.objects.filter(user=user).order_by(
            "confirmed_at"):
        ful["facts"].append(_fact("Membership confirmed",
                                  f"{mc.platform}:{mc.external_id} at "
                                  f"{mc.confirmed_at:%Y-%m-%d}",
                                  f"evidence.MembershipConfirmation:{mc.pk}"))
    for cr in CancellationRequest.objects.filter(subscription__user=user):
        ful["facts"].append(_fact("Cancellation requested",
                                  f"{cr.requested_at:%Y-%m-%d} via "
                                  f"{cr.get_channel_display()}",
                                  f"evidence.CancellationRequest:{cr.pk}"))
    if not ful["facts"]:
        ful["facts"].append(_fact("Fulfillment records", "None found", ""))
    sections["fulfillment"] = ful

    sections["refunds"] = {"title": "Refunds", "facts": [
        _fact("Refund", f"{r.amount_cents} {r.currency} at "
              f"{r.refunded_at:%Y-%m-%d} (id {r.provider_refund_id})",
              f"payments.Refund:{r.pk}") for r in _refunds(intent)]
        or [_fact("Refunds", "None", "")]}

    if dispute.reason_category in (Dispute.ReasonCategory.FRAUD,
                                   Dispute.ReasonCategory.UNRECOGNIZED):
        matches = ce3_matches(dispute)
        sections["ce3"] = {
            "title": "Prior undisputed transactions (same credential)",
            "status": "present" if matches else "missing",
            "facts": [_fact(
                f"Txn {m['payment_intent_id'][:8]}...",
                f"{m['amount']} {m['currency']} on {m['created_at'][:10]} - "
                f"matched: {', '.join(m['matched_elements'])}",
                "evidence.PaymentAuthentication") for m in matches]
            or [_fact("Qualifying history", "None in 120-365d window", "")]}
    return sections


def draft_narrative(dispute) -> str:
    """Sentences render ONLY when their source facts exist. No source -> the
    sentence is omitted, never approximated."""
    intent = dispute.payment_intent
    lines = [
        f"Merchant response to dispute {dispute.provider_dispute_id} "
        f"({dispute.get_reason_category_display()}).",
        f"Transaction: {intent.amount} {intent.currency} for "
        f"'{intent.plan.name}' on {intent.created_at:%Y-%m-%d}.",
    ]
    agr = agreement_sentence(intent)
    if agr["available"] and dispute.reason_category in TERMS_RELEVANT:
        lines.append(agr["sentence"])
        lines.append(
            f"Offer presented at checkout: {agr['pricing_snapshot'].get('plan_name')} "
            f"- {agr['pricing_snapshot'].get('price_cents')} "
            f"{agr['pricing_snapshot'].get('currency')} per "
            f"{agr['pricing_snapshot'].get('interval')}.")
    try:
        a = intent.authentication
        if a.three_ds_result in ("authenticated", "liability_shift"):
            lines.append(f"The payment was authenticated with 3-D Secure "
                         f"(result: {a.get_three_ds_result_display()}).")
        if a.cvc_check == "pass" or a.avs_zip_check == "pass":
            lines.append("Card verification (CVC/AVS) passed.")
    except PaymentAuthentication.DoesNotExist:
        pass  # omitted - never replaced with filler
    if dispute.reason_category in (Dispute.ReasonCategory.FRAUD,
                                   Dispute.ReasonCategory.UNRECOGNIZED):
        n = len(ce3_matches(dispute))
        if n:
            lines.append(f"{n} prior undisputed transaction(s) on the same "
                         f"payment credential fall within the qualifying "
                         f"120-365 day window with matching device/IP/account "
                         f"elements.")
    confs = MembershipConfirmation.objects.filter(user=intent.user)
    if confs.exists():
        first = confs.order_by("confirmed_at").first()
        lines.append(f"Community access was granted and membership confirmed "
                     f"(first observed {first.confirmed_at:%Y-%m-%d}).")
    refunds = list(_refunds(intent))
    if refunds:
        r = refunds[-1]
        lines.append(f"A refund of {r.amount_cents} {r.currency} was issued on "
                     f"{r.refunded_at:%Y-%m-%d} (reference "
                     f"{r.provider_refund_id}).")
    lines.append("All statements above are backed by the referenced system "
                 "records; the full timeline and source references accompany "
                 "this response.")
    return "\n\n".join(lines)


@transaction.atomic
def generate_package(dispute, actor=None) -> DisputeEvidencePackage:
    readiness = evaluate_readiness(dispute)
    pkg = DisputeEvidencePackage.objects.create(
        dispute=dispute, generator_version=GENERATOR_VERSION,
        reason_category=dispute.reason_category,
        sections=build_sections(dispute), timeline=build_timeline(dispute),
        narrative_draft=draft_narrative(dispute),
        readiness=readiness, generated_by=actor)
    _log(dispute, DisputeEvent.EventType.EVIDENCE_GENERATED, actor=actor,
         note=f"package {str(pkg.pk)[:8]} readiness={readiness['state']}")
    if readiness["state"] == "COMPLETE" and dispute.status in (
            Dispute.Status.OPENED, Dispute.Status.EVIDENCE_PREP):
        _transition(dispute, Dispute.Status.READY_FOR_REVIEW, actor=actor,
                    note="Evidence package complete")
    elif dispute.status == Dispute.Status.OPENED:
        _transition(dispute, Dispute.Status.EVIDENCE_PREP, actor=actor,
                    note=f"Gaps: {readiness['missing_critical']}")
    return pkg


@transaction.atomic
def submit_package(pkg, actor, via="dashboard", reference=""):
    if pkg.submitted_at:
        raise ValueError("Package already submitted; generate a new one.")
    DisputeEvidencePackage.objects.filter(pk=pkg.pk).update(
        narrative_final=pkg.narrative_draft, submitted_at=timezone.now(),
        submitted_by=actor, submitted_via=via,
        submission_reference=reference)
    pkg.refresh_from_db()
    _log(pkg.dispute, DisputeEvent.EventType.EVIDENCE_SUBMITTED, actor=actor,
         note=f"via {via} ref={reference}")
    if pkg.dispute.status in (Dispute.Status.OPENED, Dispute.Status.EVIDENCE_PREP,
                              Dispute.Status.READY_FOR_REVIEW):
        _transition(pkg.dispute, Dispute.Status.SUBMITTED, actor=actor,
                    note=f"Package {str(pkg.pk)[:8]} submitted")
    return pkg
