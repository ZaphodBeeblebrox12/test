"""
transactional.py — Transactional/system Telegram messaging layer (Stage 2).

Sits ABOVE the reusable transport (Stage 1) and BELOW business flows:

    payment / activation / linking flow
        -> apps.bot_integration.signals (on_commit edge detection)
        -> TransactionalTelegramService            <- THIS MODULE
        -> TelegramBotService.send_message_result
        -> TelegramMessageTransport

Guarantees:
  * TRANSACTIONAL ONLY. No marketing-consent gate, no suppression checks,
    no campaign logic. Transactional messages must remain usable when
    marketing is disabled — which is exactly why this module never
    consults consent.
  * FAILURE-SAFE. Telegram delivery NEVER breaks the parent operation
    (payment confirmation, activation, linking). Every exception is
    caught and converted into a failed/skipped result.
  * NO FALSE SENT. Indeterminate transport outcomes are surfaced, never
    recorded as success. message_id is returned as provider evidence.

Delivery semantics: Telegram sendMessage has no provider-side idempotency
key — exactly-once delivery is NOT promised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings

from .services.telegram import TelegramBotService
from .services.telegram_transport import TelegramMessageResult

logger = logging.getLogger(__name__)

SETTING_ENABLED = "TELEGRAM_TRANSACTIONAL_ENABLED"


@dataclass
class TransactionalSendResult:
    """Outcome of one transactional send attempt (post edge-trigger)."""

    sent: bool = False
    skipped: bool = False
    skip_reason: str = ""
    message_id: Optional[int] = None
    outcome: str = ""
    error_code: str = ""
    error_message: str = ""

    @classmethod
    def from_transport(cls, result: TelegramMessageResult) -> "TransactionalSendResult":
        return cls(
            sent=result.ok,
            skipped=False,
            message_id=result.message_id if result.ok else None,
            outcome=result.outcome,
            error_code=result.error_code,
            error_message=result.error_message,
        )


def _enabled() -> bool:
    return bool(getattr(settings, SETTING_ENABLED, True))


# ── Message templates (plain text; no external template deps) ─────────────

def render_welcome(username: str) -> str:
    return (
        f"👋 Welcome, {username}!\n\n"
        "Your Telegram account is now linked. You'll receive "
        "account notifications here (payment confirmations, subscription "
        "updates, expiry reminders).\n\n"
        "Reply /stop anytime to manage notifications."
    )


def render_subscription_activated(username: str, plan_name: str,
                                  end_date: Optional[str] = None) -> str:
    lines = [f"✅ Subscription activated — {plan_name}"]
    if end_date:
        lines.append(f"Valid until: {end_date}")
    lines.append("")
    lines.append(f"Enjoy, {username}!")
    return "\n".join(lines)


def render_payment_confirmed(plan_name: str, amount: Optional[str] = None,
                             currency: str = "") -> str:
    # Provided for flows that distinguish payment from activation. Not
    # auto-wired in Stage 2: payment and activation share one edge in
    # this codebase, so wiring both would double-message.
    paid = f"{currency}{amount}" if amount else "your payment"
    return f"💳 Payment confirmed: {paid}\nPlan: {plan_name}\nThank you!"


def render_invoice_notification(invoice_ref: str, amount: str,
                                currency: str = "", due_date: Optional[str] = None) -> str:
    # Provided for future invoice flows; no invoice edge exists yet.
    lines = [f"🧾 Invoice {invoice_ref}", f"Amount: {currency}{amount}"]
    if due_date:
        lines.append(f"Due: {due_date}")
    return "\n".join(lines)


def render_subscription_expired(username: str, plan_name: str) -> str:
    return (
        f"⏰ Your {plan_name} subscription has expired, {username}.\n\n"
        "Renew anytime to restore access."
    )


# ── Service ────────────────────────────────────────────────────────────────

class TransactionalTelegramService:
    """
    Transactional/system sender. NOT for marketing — no consent gate is
    or may be applied here. All methods never raise.
    """

    @classmethod
    def _resolve_chat_id(cls, user):
        try:
            account = getattr(user, "telegram_account", None)
        except Exception as exc:  # related-object lookup must never break flows
            logger.warning("telegram_account lookup failed: %s", type(exc).__name__)
            return None, "telegram_account lookup failed"
        if account is None:
            return None, "no linked telegram account"
        if not getattr(account, "is_active", False):
            return None, "telegram account inactive"
        chat_id = getattr(account, "chat_id", None)
        if chat_id is None:
            return None, "telegram account has no chat_id"
        return chat_id, ""

    @classmethod
    def send_text(cls, user, text: str) -> TransactionalSendResult:
        """Core send. Never raises; failure never affects the caller's flow."""
        if not _enabled():
            return TransactionalSendResult(skipped=True,
                                           skip_reason="transactional telegram disabled")
        chat_id, reason = cls._resolve_chat_id(user)
        if chat_id is None:
            logger.info("transactional telegram skipped: %s", reason)
            return TransactionalSendResult(skipped=True, skip_reason=reason)
        try:
            result = TelegramBotService.send_message_result(chat_id, text)
        except Exception as exc:
            # The transport is designed not to raise; this is belt-and-braces
            # so a Telegram problem can NEVER roll back a payment/activation.
            logger.exception("transactional telegram send raised; swallowing")
            return TransactionalSendResult(
                sent=False, outcome="error", error_code="unexpected_error",
                error_message=type(exc).__name__)
        send_result = TransactionalSendResult.from_transport(result)
        if not send_result.sent:
            logger.warning(
                "transactional telegram not delivered: outcome=%s code=%s",
                send_result.outcome, send_result.error_code)
        return send_result

    @classmethod
    def send_welcome(cls, user) -> TransactionalSendResult:
        username = getattr(user, "username", None) or "trader"
        return cls.send_text(user, render_welcome(username))

    @classmethod
    def send_subscription_activated(cls, user, plan_name: str = "your plan") -> TransactionalSendResult:
        username = getattr(user, "username", None) or "trader"
        return cls.send_text(user, render_subscription_activated(username, plan_name))

    @classmethod
    def send_payment_confirmed(cls, user, plan_name: str,
                               amount: Optional[str] = None, currency: str = "") -> TransactionalSendResult:
        return cls.send_text(user, render_payment_confirmed(plan_name, amount, currency))

    @classmethod
    def send_invoice_notification(cls, user, invoice_ref: str, amount: str,
                                  currency: str = "", due_date: Optional[str] = None) -> TransactionalSendResult:
        return cls.send_text(user, render_invoice_notification(invoice_ref, amount, currency, due_date))

    @classmethod
    def send_subscription_expired(cls, user, plan_name: str = "your plan") -> TransactionalSendResult:
        username = getattr(user, "username", None) or "trader"
        return cls.send_text(user, render_subscription_expired(username, plan_name))

    @classmethod
    def send_expiry_reminder(cls, user, plan_name: str, days_left: int) -> TransactionalSendResult:
        username = getattr(user, "username", None) or "trader"
        return cls.send_text(user, render_expiry_reminder(username, plan_name, days_left))

    @classmethod
    def send_access_removed(cls, user, plan_name: str) -> TransactionalSendResult:
        username = getattr(user, "username", None) or "trader"
        return cls.send_text(user, render_access_removed(username, plan_name))

    send_expiry_reminder = send_expiry_reminder
    send_access_removed = send_access_removed



# ── Stage 3: expiry reminder / access-removed senders (TRANSACTIONAL) ─────


def render_expiry_reminder(username: str, plan_name: str, days_left: int) -> str:
    return (
        f"⏳ Reminder: your {plan_name} subscription expires in "
        f"{days_left} days, {username}.\n\n"
        "Renew before it expires to keep uninterrupted access."
    )


def render_access_removed(username: str, plan_name: str) -> str:
    return (
        f"🔒 Your {plan_name} access has ended, {username}.\n\n"
        "Renew anytime to restore access to all channels."
    )
