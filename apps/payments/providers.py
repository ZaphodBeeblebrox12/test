"""Minimal hosted-checkout provider layer (Stripe + Razorpay).

P1 scope: create hosted checkouts/orders from the immutable PaymentIntent
snapshot, and verify a specific payment server-side before activation.
No webhooks, no recurring engine -- see architecture review phase plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.conf import settings


class ProviderError(Exception):
    """Provider API unreachable or returned an unusable response."""


class ProviderConfigurationError(ProviderError):
    """Provider SDK/credentials not configured."""


@dataclass
class CreatedCheckout:
    provider_reference: str   # Stripe Checkout Session id / Razorpay Order id
    checkout_url: Optional[str] = None  # Stripe hosted url (Razorpay uses Checkout.js)


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

def _stripe():
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ProviderConfigurationError(
            "stripe SDK is not installed (pip install stripe)") from exc
    key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
    if not key:
        raise ProviderConfigurationError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = key
    return stripe


def create_stripe_checkout(intent, success_url: str, cancel_url: str) -> CreatedCheckout:
    """Create a hosted Checkout Session for the snapshot amount/currency."""
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(intent.id),
            line_items=[{
                "price_data": {
                    "currency": intent.currency.lower(),
                    "unit_amount": int(intent.amount),
                    "product_data": {"name": f"{intent.plan.name} subscription"},
                },
                "quantity": 1,
            }],
            metadata={"payment_intent_id": str(intent.id)},
        )
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"stripe checkout session create failed: {exc}") from exc
    return CreatedCheckout(
        provider_reference=session["id"],
        checkout_url=session.get("url"),
    )


def get_stripe_checkout_url(session_id: str) -> Optional[str]:
    """Hosted session url for the checkout page (re-fetch if not stored)."""
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        raise ProviderError(f"stripe session retrieve failed: {exc}") from exc
    return session.get("url")


def verify_stripe_payment(intent) -> Optional[bool]:
    """True = paid, False = definitively failed/canceled, None = unknown."""
    if not intent.provider_reference:
        return None
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(intent.provider_reference)
    except Exception:
        return None
    if session.get("status") == "complete":
        if session.get("payment_status") == "paid":
            amount = session.get("amount_total")
            currency = (session.get("currency") or "").upper()
            if amount is not None and amount != intent.amount:
                return False
            if currency and currency != intent.currency.upper():
                return False
            return True
        return False  # complete but unpaid (e.g. payment failed)
    if session.get("status") == "expired":
        return False
    return None  # open -> pending


# ---------------------------------------------------------------------------
# Razorpay
# ---------------------------------------------------------------------------

def _razorpay_client():
    try:
        import razorpay
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ProviderConfigurationError(
            "razorpay SDK is not installed (pip install razorpay)") from exc
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "") or ""
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "") or ""
    if not key_id or not key_secret:
        raise ProviderConfigurationError("RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET not configured")
    return razorpay.Client(auth=(key_id, key_secret))


def create_razorpay_order(intent) -> CreatedCheckout:
    """Create a Razorpay order for the snapshot amount/currency."""
    client = _razorpay_client()
    try:
        order = client.order.create(data={
            "amount": int(intent.amount),
            "currency": intent.currency.upper(),
            "receipt": str(intent.id)[:40],
            "notes": {"payment_intent_id": str(intent.id)},
        })
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"razorpay order create failed: {exc}") from exc
    return CreatedCheckout(provider_reference=order["id"])


def verify_razorpay_payment(intent) -> Optional[bool]:
    """True = order paid, False = failed, None = created/attempted (pending)."""
    if not intent.provider_reference:
        return None
    client = _razorpay_client()
    try:
        order = client.order.fetch(intent.provider_reference)
    except Exception:
        return None
    amount = order.get("amount")
    currency = (order.get("currency") or "").upper()
    if amount is not None and amount != intent.amount:
        return False
    if currency and currency != intent.currency.upper():
        return False
    status = order.get("status")
    if status == "paid":
        return True
    if status in ("created", "attempted"):
        return None
    return False


# ---------------------------------------------------------------------------

def create_hosted_checkout(intent, success_url: str, cancel_url: str) -> CreatedCheckout:
    """Dispatch to the intent's provider."""
    if intent.provider == "razorpay":
        return create_razorpay_order(intent)
    return create_stripe_checkout(intent, success_url, cancel_url)


def verify_provider_payment(intent) -> Optional[bool]:
    """Dispatch verification. True/False/None (unknown/pending)."""
    if intent.provider == "razorpay":
        return verify_razorpay_payment(intent)
    return verify_stripe_payment(intent)
