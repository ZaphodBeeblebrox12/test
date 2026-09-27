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
            # Provider-native consent banner (appears in the Stripe-hosted
            # page and in dashboard session records). Our own checkout
            # consent step remains the primary evidentiary record.
            consent_collection={"terms_of_service": "required"},
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


# ---------------------------------------------------------------------------
# Payment-authentication backfill (evidence layer)
# ---------------------------------------------------------------------------

_THREEDS_MAP = {
    "attempted": "attempted",
    "authenticated": "authenticated",
    "failed": "failed",
    "not_supported": "not_requested",
}


def fetch_stripe_authentication(intent) -> Optional[dict]:
    """Retrieve the charge's payment_method_details once for evidence.

    Read-only API calls; blanks are honest (never invent ECI/CAVV etc.).
    """
    if not intent.provider_reference:
        return None
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(intent.provider_reference)
        pi_id = session.get("payment_intent")
        if not pi_id:
            return None
        charges = stripe.Charge.list(payment_intent=pi_id, limit=3)
        charge = None
        for c in charges.auto_paging_iter():
            charge = c
            if c.get("paid"):
                break
        if charge is None:
            return None
        pmd = charge.get("payment_method_details") or {}
        card = pmd.get("card") or {}
        tds = card.get("three_d_secure") or {}
        checks = card.get("checks") or {}
        wallet = card.get("wallet") or {}
        return {
            "three_ds_result": _THREEDS_MAP.get(
                (tds.get("result") or "").lower(), "unknown"),
            # ECI is not exposed via the Charges API for 3DS2; never invent it.
            "eci": "",
            # The cryptogram (CAVV) presence IS exposed.
            "cavv_present": bool(tds.get("cryptogram")),
            "cvc_check": checks.get("cvc_check") or "",
            "avs_line1_check": checks.get("address_line1_check") or "",
            "avs_zip_check": checks.get("address_zip_check") or "",
            "card_brand": card.get("brand") or "",
            "card_last4": card.get("last4") or "",
            "card_fingerprint": card.get("fingerprint") or "",
            "card_country": (card.get("country") or "").upper(),
            "card_issuer": card.get("issuer") or "",
            "wallet_type": wallet.get("type") or "",
            "network_txn_id": (charge.get("payment_method_details") or {})
                              .get("card", {}).get("network_transaction_id") or "",
            "source_ref": charge.get("id") or "",
            "raw": {"charge_id": charge.get("id"),
                    "three_d_secure_result": tds.get("result") or "",
                    "outcome_network_status": (charge.get("outcome") or {})
                                              .get("network_status") or ""},
        }
    except Exception as exc:
        raise ProviderError(f"stripe auth backfill failed: {exc}") from exc


def fetch_razorpay_authentication(intent) -> Optional[dict]:
    """Razorpay exposes sparse card/3DS data; blanks are the honest result."""
    if not intent.provider_reference:
        return None
    client = _razorpay_client()
    try:
        payments = client.order.payments(intent.provider_reference)
        items = payments.get("items") or []
        paid = [p for p in items if p.get("status") == "captured"] or items
        if not paid:
            return None
        p = paid[-1]
        method = p.get("method") or ""
        card = p.get("card") or {}
        return {
            "three_ds_result": "unknown",
            "eci": "", "cavv_present": False,
            "cvc_check": "", "avs_line1_check": "", "avs_zip_check": "",
            "card_brand": (card.get("network") or "").lower() if method == "card" else "",
            "card_last4": card.get("last4") or "",
            "card_fingerprint": card.get("fingerprint") or "",
            "card_country": "",
            "card_issuer": card.get("issuer") or "",
            "wallet_type": method if method in ("apple_pay", "google_pay") else "",
            "network_txn_id": p.get("acquirer_data", {}).get("auth_code") or "",
            "source_ref": p.get("id") or "",
            "raw": {"payment_id": p.get("id"), "method": method,
                    "authorized_at": p.get("authorized_at")},
        }
    except Exception as exc:
        raise ProviderError(f"razorpay auth backfill failed: {exc}") from exc
