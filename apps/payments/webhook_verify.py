"""P4: provider webhook signature verification (official schemes).

Stripe   : Stripe-Signature header 't=..,v1=..' -> stripe.Webhook.construct_event
           with the RAW body + endpoint secret (whsec_..).  Docs:
           https://docs.stripe.com/webhooks/signature
Razorpay : X-Razorpay-Signature header = HMAC-SHA256 hex of the RAW body,
           keyed by the webhook secret.  Docs: https://razorpay.com/docs/webhooks/

Returns the parsed event dict, or raises WebhookVerificationError.
"""
import hashlib
import hmac
import json

from django.conf import settings


class WebhookVerificationError(Exception):
    """Signature invalid / missing / malformed."""


def verify_stripe(raw_body: bytes, signature_header: str) -> dict:
    if not signature_header:
        raise WebhookVerificationError("missing Stripe-Signature header")
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""
    if not secret:
        raise WebhookVerificationError("STRIPE_WEBHOOK_SECRET not configured")
    try:
        import stripe
        return stripe.Webhook.construct_event(raw_body, signature_header, secret)
    except WebhookVerificationError:
        raise
    except Exception as exc:
        raise WebhookVerificationError(f"stripe construct_event failed: {exc}") from exc


def verify_razorpay(raw_body: bytes, signature_header: str) -> dict:
    if not signature_header:
        raise WebhookVerificationError("missing X-Razorpay-Signature header")
    secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or ""
    if not secret:
        raise WebhookVerificationError("RAZORPAY_WEBHOOK_SECRET not configured")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise WebhookVerificationError("razorpay signature mismatch")
    try:
        return json.loads(raw_body.decode() or "{}")
    except ValueError as exc:
        raise WebhookVerificationError(f"razorpay body not JSON: {exc}") from exc


def verify(provider: str, raw_body: bytes, signature_header: str) -> dict:
    if provider == "stripe":
        return verify_stripe(raw_body, signature_header)
    return verify_razorpay(raw_body, signature_header)
