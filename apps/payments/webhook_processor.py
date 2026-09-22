"""P4 + billing: idempotent webhook event processor (durable job target).

Design:
  * The payment-activation path (payment.captured / order.paid /
    payment_intent.succeeded / charge.succeeded / checkout.session.completed)
    is unchanged from P4: find intent -> activate_paid_subscription (atomic
    claim makes it exactly-once).
  * Refund events (stripe charge.refunded / razorpay refund.processed) and
    dispute events (stripe charge.dispute.*) are additive and follow the same
    discipline: find intent -> atomic conditional claims -> durable event +
    AuditLog -> entitlement via existing subscription services.
  * Terminal (processed/ignored) events are never reprocessed, so durable-job
    retries and duplicate deliveries cannot duplicate refunds, cancellations,
    history entries, or notifications.

Stripe capture note: we do NOT key on the stripe session id here. We match
via provider_payment_id (pi_..) or provider_reference (cs_..), because the
charge/webhook evidence exposes the payment-intent id, not the session id.
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.db import models

from .models import PaymentIntent, WebhookEvent
from .services import (
    activate_paid_subscription,
    apply_refund_policy,
    confirm_chargeback,
    note_provider_payment_id,
    record_dispute_opened,
    record_dispute_won,
    record_refund,
)
from apps.subscriptions.models import Subscription
from apps.subscriptions.services import extend_subscription

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = (WebhookEvent.Status.PROCESSED, WebhookEvent.Status.IGNORED)


def _mark(event, status, error=""):
    event.status = status
    event.error = error[:500]
    from django.utils import timezone
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "error", "processed_at"])


def _stripe_ts(value):
    """Stripe 'created' fields are unix seconds."""
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _rz_ts(value):
    """Razorpay 'created_at' may be unix seconds or an ISO string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _stripe_ts(value)
    try:
        from django.utils.dateparse import parse_datetime
        return parse_datetime(str(value))
    except Exception:
        return None


def _event_object(payload):
    """Primary provider object from a stored webhook payload.

    Handles both shapes stored by webhook_views:
      * Stripe full event: {"type": .., "data": {"object": {...}}}
      * Razorpay event: {"event": .., "payload": {"<kind>": {"entity": {...}}}}
        (webhook_views stores the INNER payload for Razorpay, so also probe
        top-level "<kind>" directly.)
    """
    data = payload or {}
    obj = (data.get("data") or {}).get("object")
    if isinstance(obj, dict):
        return obj
    obj = data.get("object")
    if isinstance(obj, dict):
        return obj
    inner = data.get("payload")
    if isinstance(inner, dict):
        for kind in ("payment", "refund", "order", "dispute"):
            ent = (inner.get(kind) or {}).get("entity")
            if isinstance(ent, dict):
                return ent
    return {}


def _payment_id_from(provider, payload):
    """Best-effort provider payment id (pi_.. / pay_..) from a capture event."""
    obj = _event_object(payload)
    if provider == PaymentIntent.Provider.STRIPE:
        pi = obj.get("payment_intent")
        if pi:
            return pi
        oid = obj.get("id")
        return oid if str(oid or "").startswith("pi_") else None
    oid = obj.get("id")
    return oid if str(oid or "").startswith("pay_") else None


def _find_intent(provider, provider_event_id, payload):
    """Best-effort link from webhook payload to PaymentIntent (P4).

    We try to match via the provider payment/charge id, the checkout
    session/order id, or the payment id itself. The payment id is preferred
    because it is what refund/dispute evidence exposes.
    """
    data = payload or {}
    candidates = []

    obj = _event_object(payload)
    if obj:
        candidates += [obj.get("payment_intent"), obj.get("charge"),
                       obj.get("order_id"), obj.get("payment_id"), obj.get("id")]

    # Razorpay top-level probes (webhook_views stores the inner payload).
    containers = [data]
    if isinstance(data.get("payload"), dict):
        containers.append(data["payload"])
    for container in containers:
        pay = container.get("payment", {}).get("entity", {})
        if isinstance(pay, dict):
            candidates += [pay.get("id"), pay.get("order_id")]
        order = container.get("order", {}).get("entity", {})
        if isinstance(order, dict):
            candidates += [order.get("id"), order.get("receipt")]
        refund = container.get("refund", {}).get("entity", {})
        if isinstance(refund, dict):
            candidates += [refund.get("payment_id"), refund.get("order_id")]

    for ref in filter(None, candidates):
        hit = (PaymentIntent.objects
               .filter(provider=provider)
               .filter(models.Q(provider_reference=ref)
                       | models.Q(provider_payment_id=ref))
               .first())
        if hit is not None:
            return hit
    return None


def _process_payment_captured(event, payload):
    intent = _find_intent(event.provider, event.provider_event_id, payload)
    if intent is None:
        _mark(event, WebhookEvent.Status.IGNORED, "no matching PaymentIntent")
        return
    note_provider_payment_id(intent, _payment_id_from(event.provider, payload))
    activated, sub = activate_paid_subscription(intent)
    _mark(event, WebhookEvent.Status.PROCESSED,
          f"activated={activated} subscription={sub and sub.id}")


def _process_renewal(event, payload):
    """P5: a provider subscription renewed -> extend the existing entitlement."""
    data = payload or {}
    # Stored payload may be the full event OR its inner 'payload' object.
    sub_ref = ""
    for container in (data, data.get("payload", {})):
        if not isinstance(container, dict):
            continue
        ent = container.get("subscription", {}).get("entity")
        if isinstance(ent, dict) and ent.get("id"):
            sub_ref = ent["id"]
            break
        if container.get("subscription", {}).get("id"):
            sub_ref = container["subscription"]["id"]
            break
    sub = Subscription.objects.filter(
        provider_subscription_id=sub_ref, is_active=True).first() if sub_ref else None
    if sub is None:
        _mark(event, WebhookEvent.Status.IGNORED, "no matching active Subscription")
        return
    extended = extend_subscription(sub, days=30, actor="webhook")
    _mark(event, WebhookEvent.Status.PROCESSED, f"extended={extended}")


def _process_refund(event, payload):
    """Record a provider refund. Idempotent per provider refund id.

    Stripe: charge.refunded carries charge.refunds.data — every refund on
    the charge with its own id and amount; each is stored once (a second
    delivery of the same refund id is a no-op). Falls back to an
    amount_refunded delta when no refund list is present.
    Razorpay: refund.processed carries payload.refund.entity with id, amount
    (minor units) and payment_id.
    """
    intent = _find_intent(event.provider, event.provider_event_id, payload)
    if intent is None:
        _mark(event, WebhookEvent.Status.IGNORED, "no matching PaymentIntent")
        return

    refunds = []  # (provider_refund_id, amount_cents, currency, refunded_at)
    if event.provider == PaymentIntent.Provider.STRIPE:
        charge = _event_object(payload)
        currency = (charge.get("currency") or intent.currency or "USD").upper()
        data = (charge.get("refunds") or {}).get("data") or []
        for r in data:
            if not isinstance(r, dict):
                continue
            refunds.append((
                r.get("id") or f'{charge.get("id")}:{event.provider_event_id}',
                int(r.get("amount") or 0),
                (r.get("currency") or currency).upper(),
                _stripe_ts(r.get("created")),
            ))
        if not refunds and charge:
            absolute = int(charge.get("amount_refunded") or 0)
            delta = absolute - intent.refunded_cents
            if delta > 0:
                refunds.append((
                    f'{charge.get("id")}:{event.provider_event_id}',
                    delta, currency, None))
    else:
        ent = None
        inner = (payload or {}).get("payload")
        if isinstance(inner, dict):
            ent = (inner.get("refund") or {}).get("entity")
        if ent is None and isinstance((payload or {}).get("refund"), dict):
            ent = (payload["refund"] or {}).get("entity")
        if isinstance(ent, dict):
            note_provider_payment_id(intent, ent.get("payment_id"))
            refunds.append((
                ent.get("id"),
                int(ent.get("amount") or 0),
                (ent.get("currency") or intent.currency or "USD").upper(),
                _rz_ts(ent.get("created_at")),
            ))

    refunds = [r for r in refunds if r[0] and r[1] > 0]
    if not refunds:
        _mark(event, WebhookEvent.Status.IGNORED, "no refund data in payload")
        return

    created_count = 0
    for provider_refund_id, amount_cents, currency, refunded_at in refunds:
        _refund, created = record_refund(
            intent, provider=event.provider,
            provider_refund_id=str(provider_refund_id),
            amount_cents=amount_cents, currency=currency,
            refunded_at=refunded_at, source="webhook",
            metadata={"webhook_event_id": event.provider_event_id})
        created_count += int(created)

    if created_count:
        intent.refresh_from_db()
        apply_refund_policy(intent)
        from .notifications import notify_refunded
        notify_refunded(intent)
    _mark(event, WebhookEvent.Status.PROCESSED, f"new_refunds={created_count}")


def _process_dispute(event, payload):
    """Stripe dispute lifecycle.

      * charge.dispute.created  -> flag disputed (NOT confirmed): audit +
        durable event only. Entitlement is NOT touched; the dispute can be
        won. (Razorpay sends no dispute webhooks — use the admin action.)
      * charge.dispute.funds_withdrawn, or charge.dispute.closed with
        status=lost -> CONFIRMED chargeback: cancel subscription + revoke
        access through the existing cancel flow, notify once.
      * charge.dispute.closed with status=won -> clear flags.
    """
    intent = _find_intent(event.provider, event.provider_event_id, payload)
    if intent is None:
        _mark(event, WebhookEvent.Status.IGNORED, "no matching PaymentIntent")
        return
    dispute = _event_object(payload)
    dispute_id = dispute.get("id") or ""
    status = (dispute.get("status") or "").lower()
    et = event.event_type

    if et == "charge.dispute.closed" and status == "won":
        record_dispute_won(intent, dispute_reference=dispute_id)
        _mark(event, WebhookEvent.Status.PROCESSED, "dispute won")
        return

    confirmed = et == "charge.dispute.funds_withdrawn" or (
        et == "charge.dispute.closed" and status == "lost")
    if confirmed:
        performed = confirm_chargeback(intent, dispute_reference=dispute_id)
        if performed:
            from .notifications import notify_chargedback
            notify_chargedback(intent)
        _mark(event, WebhookEvent.Status.PROCESSED,
              f"chargeback confirmed performed={performed}")
        return

    record_dispute_opened(intent, dispute_reference=dispute_id)
    _mark(event, WebhookEvent.Status.PROCESSED, "dispute recorded (not confirmed)")


def process_webhook_event(payload, job):
    """P4 durable job entrypoint (dedupe + at-most-once semantics)."""
    event_id = (payload or {}).get("webhook_event_id")
    event = WebhookEvent.objects.filter(pk=event_id).first() if event_id else None
    if event is None:
        job.mark_failed(error="webhook event not found")
        return

    # At-most-once: terminal events are never reprocessed. This, combined
    # with the atomic claims in the services above, is what makes retries
    # and duplicate deliveries unable to duplicate side effects.
    if event.status in _TERMINAL_STATUSES:
        return

    try:
        et = event.event_type
        if et in ("payment_intent.succeeded", "charge.succeeded", "payment.captured",
                  "order.paid", "checkout.session.completed"):
            _process_payment_captured(event, event.payload)
        elif et in ("invoice.paid", "subscription.charged"):
            _process_renewal(event, event.payload)
        elif et in ("charge.refunded", "refund.processed"):
            _process_refund(event, event.payload)
        elif et in ("charge.dispute.created", "charge.dispute.funds_withdrawn",
                    "charge.dispute.closed"):
            _process_dispute(event, event.payload)
        else:
            _mark(event, WebhookEvent.Status.IGNORED, f"unhandled event {et}")
    except Exception as exc:
        logger.exception("webhook event %s processing failed", event.pk)
        _mark(event, WebhookEvent.Status.FAILED, str(exc))
        raise
