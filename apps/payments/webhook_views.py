"""P4: provider webhook endpoints (receive -> durable event -> ack).

Receive is lightweight: authenticate, identify, durably store the event
(get-or-create on provider+event_id for idempotency), enqueue processing via
the durable job, and acknowledge.  Business mutation happens in the processor
(apps/payments/webhook_processor) through the shared services -- never here.
"""
import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from .models import WebhookEvent
from apps.jobs.enqueue import enqueue_generic

logger = logging.getLogger(__name__)


def _receive(request, provider):
    from . import webhook_verify
    raw = request.body
    header = (request.headers.get("Stripe-Signature")
              if provider == "stripe"
              else request.headers.get("X-Razorpay-Signature"))
    try:
        event = webhook_verify.verify(provider, raw, header or "")
    except webhook_verify.WebhookVerificationError as exc:
        return HttpResponseBadRequest(f"invalid signature: {exc}")

    provider_event_id = event.get("id") or ""
    event_type = event.get("event") or event.get("type") or ""
    if not provider_event_id:
        return HttpResponseBadRequest("missing provider event id")
    # Durable idempotent store: get-or-create; duplicates reuse the same row.
    webhook_event, created = WebhookEvent.objects.get_or_create(
        provider=provider,
        provider_event_id=provider_event_id,
        defaults={"event_type": event_type, "payload": event.get("payload") or event},
    )
    if created:
        enqueue_generic("process_webhook_event",
                        {"webhook_event_id": str(webhook_event.id)},
                        idempotency_key=f"wh:{webhook_event.id}")
    return HttpResponse("ok", status=200)


@csrf_exempt
def stripe_webhook(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    return _receive(request, "stripe")


@csrf_exempt
def razorpay_webhook(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    return _receive(request, "razorpay")
