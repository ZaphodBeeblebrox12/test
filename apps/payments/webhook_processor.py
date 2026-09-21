"""P4: idempotent webhook event processor (durable job 'process_webhook_event').

Maps provider events to the shared lifecycle service.  Only payment
completion and subscription-renewal events mutate business state; others are
marked ignored.  All mutations are idempotent via the services' atomic claims.
"""
import logging

from .models import PaymentIntent, WebhookEvent
from .services import activate_paid_subscription
from apps.subscriptions.models import Subscription
from apps.subscriptions.services import extend_subscription

logger = logging.getLogger(__name__)


def _mark(event, status, error=""):
    event.status = status
    event.error = error[:500]
    from django.utils import timezone
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "error", "processed_at"])


def _find_intent(provider, provider_event_id, payload):
    """Locate the PaymentIntent via provider references in the payload.

    provider_reference stores the provider's order/session id (Razorpay) or
    Checkout Session id (Stripe).  A payment.captured webhook carries the
    *payment* entity id, which can differ from the order id -- so check the
    top-level object id, the payment/order entity ids, and the raw id.
    """
    data = payload or {}
    candidates = []
    # The stored payload may be the full event OR its inner 'payload' object,
    # so probe both the event-level and inner shapes.
    obj = data.get("object")
    if isinstance(obj, dict):
        candidates.append(obj.get("id"))
    for container in (data, data.get("payload", {})):
        if not isinstance(container, dict):
            continue
        pay = container.get("payment", {}).get("entity")
        if isinstance(pay, dict):
            candidates.append(pay.get("id"))
            candidates.append(pay.get("order_id"))
        order = container.get("order", {}).get("entity")
        if isinstance(order, dict):
            candidates.append(order.get("id"))
    candidates.append(data.get("id"))
    for ref in filter(None, candidates):
        hit = PaymentIntent.objects.filter(provider=provider,
                                           provider_reference=ref).first()
        if hit:
            return hit
    return None


def _process_payment_captured(event, payload):
    intent = _find_intent(event.provider, event.provider_event_id, payload)
    if intent is None:
        _mark(event, WebhookEvent.Status.IGNORED, "no matching PaymentIntent")
        return
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


def process_webhook_event(payload, job):
    from django.utils import timezone
    event_id = (payload or {}).get("webhook_event_id")
    event = WebhookEvent.objects.filter(pk=event_id).first() if event_id else None
    if event is None:
        job.mark_failed(error="webhook event not found")
        return
    try:
        et = event.event_type
        if et in ("payment_intent.succeeded", "charge.succeeded",
                  "payment.captured", "order.paid"):
            _process_payment_captured(event, event.payload)
        elif et in ("invoice.paid", "subscription.charged"):
            _process_renewal(event, event.payload)
        else:
            _mark(event, WebhookEvent.Status.IGNORED, f"unhandled event {et}")
    except Exception as exc:  # noqa: BLE001 - durable job retries on failure
        _mark(event, WebhookEvent.Status.FAILED, str(exc))
        raise
