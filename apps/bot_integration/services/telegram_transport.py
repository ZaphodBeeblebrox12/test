"""
telegram_transport.py — Reusable low-level Telegram message transport.

Stage 1 foundation: a clean send capability reusable by BOTH marketing and
transactional/system messaging (welcome, invoice, payment confirmation,
subscription activation/expiry, announcements, campaign sends).

Scope of this module (deliberately narrow):
  * chat_id + message text  ->  Telegram Bot API sendMessage
  * timeout / response handling
  * error classification: definite rejection vs indeterminate
  * message_id extraction

This module knows NOTHING about:
  * Campaigns / CampaignRecipient / campaign sweeps
  * marketing consent or suppression rules
  * subscriptions, invoices, referrals
  * channel grants / entitlements (Provision Contract v1 owns those)

Consent boundary (critical):
  Business-layer services decide whether a message is marketing or
  transactional. Marketing consent checks live ABOVE this transport.
  This transport applies NO consent gate, so transactional callers can
  always use it without marketing logic interfering.

Delivery semantics:
  Telegram's sendMessage has NO provider-side idempotency key, so this
  transport NEVER claims exactly-once delivery. Every call returns a
  TelegramMessageResult distinguishing:

    outcome="accepted"       — provider returned ok=True; message_id captured
    outcome="rejected"       — provider returned a definite API error
    outcome="indeterminate"  — timeout / network failure / unparseable
                               response; the request MAY have been processed
                               by Telegram — never report as SENT

Security:
  The bot token appears in the request URL, so requests exception strings
  MAY contain it. This module therefore builds all error messages itself
  and NEVER propagates raw exception text.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Union

import requests

logger = logging.getLogger(__name__)

# ── Outcomes ─────────────────────────────────────────────────────────────
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_INDETERMINATE = "indeterminate"

# ── Error codes ──────────────────────────────────────────────────────────
ERR_CONFIG = "config_error"             # bot token missing / not configured
ERR_MISSING_CHAT_ID = "missing_chat_id" # chat_id required — never fabricated
ERR_TIMEOUT = "timeout"
ERR_NETWORK = "network_error"
ERR_MALFORMED_RESPONSE = "malformed_response"  # body could not be interpreted
ERR_API_REJECTED = "api_rejected"       # definite Telegram API rejection
ERR_RATE_LIMITED = "rate_limited"       # persistent 429 after flood retries

DEFAULT_TIMEOUT = (5, 10)   # (connect, read) — matches prior behavior
MAX_FLOOD_RETRIES = 3
# Bound flood-wait sleeps so a webhook request thread is never parked for
# minutes by an aggressive retry_after value. The retry still happens.
MAX_FLOOD_SLEEP_SECONDS = 30


@dataclass
class TelegramMessageResult:
    """
    Rich result for one send_message call (after any flood retries).

    `ok` is True ONLY for a definite provider acceptance. An indeterminate
    outcome is never reported as success, so higher layers never persist a
    false SENT state. No campaign-specific data is stored here.
    """

    outcome: str                                    # accepted | rejected | indeterminate
    message_id: Optional[int] = None                # provider message_id when accepted
    error_code: str = ""                            # one of the ERR_* constants
    error_message: str = ""                         # sanitized — never contains the token
    http_status: Optional[int] = None
    telegram_error_code: Optional[int] = None       # provider error_code when rejected
    retry_after: Optional[int] = None               # provider flood-wait hint (429 only)
    attempts: int = 1                               # total API attempts incl. flood retries

    @property
    def ok(self) -> bool:
        """Definite provider acceptance only."""
        return self.outcome == OUTCOME_ACCEPTED

    @property
    def definite(self) -> bool:
        """True when Telegram's answer is known (accepted or rejected)."""
        return self.outcome in (OUTCOME_ACCEPTED, OUTCOME_REJECTED)

    @property
    def retryable(self) -> bool:
        """True when a later attempt could change the outcome."""
        return self.outcome == OUTCOME_INDETERMINATE or self.error_code == ERR_RATE_LIMITED

    def __str__(self) -> str:
        if self.ok:
            return f"TelegramMessageResult(accepted, message_id={self.message_id})"
        return (f"TelegramMessageResult({self.outcome}, "
                f"error_code={self.error_code}, "
                f"message={self.error_message!r})")


class TelegramMessageTransport:
    """
    Stateless low-level Telegram Bot API message transport.

    Usage is identical for every caller class — consent gates live above:

        transport = TelegramMessageTransport(token=token)
        result = transport.send_message(chat_id=chat_id, text=text)
        if result.ok:
            store result.message_id           # provider evidence
        elif result.outcome == "indeterminate":
            ...                               # unknown — do NOT record SENT
    """

    api_base = "https://api.telegram.org"

    def __init__(
        self,
        token: str,
        timeout=DEFAULT_TIMEOUT,
        max_flood_retries: int = MAX_FLOOD_RETRIES,
    ):
        self.token = token
        self.timeout = timeout
        self.max_flood_retries = max_flood_retries

    # ── public API ───────────────────────────────────────────────────────

    def send_message(self, chat_id: Union[int, str], text: str) -> TelegramMessageResult:
        """
        Send a text message. Returns a TelegramMessageResult — never raises
        for provider/transport failures and never fabricates a chat_id.
        """
        if not self.token:
            return TelegramMessageResult(
                outcome=OUTCOME_INDETERMINATE,
                error_code=ERR_CONFIG,
                error_message="Telegram bot token is not configured",
            )
        if chat_id is None or isinstance(chat_id, bool) or str(chat_id).strip() == "":
            return TelegramMessageResult(
                outcome=OUTCOME_REJECTED,
                error_code=ERR_MISSING_CHAT_ID,
                error_message="chat_id is required and was not provided",
            )

        payload = {"chat_id": chat_id, "text": text}
        url = f"{self.api_base}/bot{self.token}/sendMessage"

        attempts = 0
        last_rejection: Optional[TelegramMessageResult] = None

        for attempt in range(1, self.max_flood_retries + 1):
            attempts = attempt
            result = self._single_attempt(url, payload)
            result.attempts = attempts

            if result.outcome != OUTCOME_REJECTED or result.telegram_error_code != 429:
                return result

            # Flood wait (429): honor provider retry_after, then retry.
            last_rejection = result
            if attempt < self.max_flood_retries:
                retry_after = result.retry_after if result.retry_after is not None else 1
                sleep_for = min(retry_after, MAX_FLOOD_SLEEP_SECONDS)
                logger.warning(
                    "Telegram flood wait %ss, retry %d/%d",
                    retry_after, attempt, self.max_flood_retries,
                )
                if sleep_for > 0:
                    time.sleep(sleep_for)

        # Persistent 429 after all retries: still a definite provider answer.
        last_rejection.error_code = ERR_RATE_LIMITED
        last_rejection.error_message = (
            f"Telegram rate limit (429) persisted after "
            f"{self.max_flood_retries} attempts"
        )
        return last_rejection

    # ── internals ────────────────────────────────────────────────────────

    def _single_attempt(self, url: str, payload: dict) -> TelegramMessageResult:
        """
        One HTTP call. Every error path builds its own sanitized message —
        raw exception text is never propagated (it can embed the token URL).
        """
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.Timeout:
            return TelegramMessageResult(
                outcome=OUTCOME_INDETERMINATE,
                error_code=ERR_TIMEOUT,
                error_message="Request to Telegram API timed out",
            )
        except requests.exceptions.RequestException as exc:
            # NOTE: str(exc) may contain the bot token (request URL) —
            # log/return only the exception TYPE.
            logger.warning("Telegram API request failed: %s", type(exc).__name__)
            return TelegramMessageResult(
                outcome=OUTCOME_INDETERMINATE,
                error_code=ERR_NETWORK,
                error_message=f"Network error contacting Telegram API ({type(exc).__name__})",
            )

        http_status = resp.status_code
        try:
            data = resp.json()
        except ValueError:
            return TelegramMessageResult(
                outcome=OUTCOME_INDETERMINATE,
                error_code=ERR_MALFORMED_RESPONSE,
                error_message=f"Telegram returned an unparseable body (HTTP {http_status})",
                http_status=http_status,
            )

        if not isinstance(data, dict) or "ok" not in data:
            return TelegramMessageResult(
                outcome=OUTCOME_INDETERMINATE,
                error_code=ERR_MALFORMED_RESPONSE,
                error_message=f"Telegram response missing 'ok' field (HTTP {http_status})",
                http_status=http_status,
            )

        if data["ok"]:
            message_id = self._extract_message_id(data.get("result"))
            return TelegramMessageResult(
                outcome=OUTCOME_ACCEPTED,
                message_id=message_id,
                http_status=http_status,
            )

        # Definite provider rejection.
        tg_error_code = data.get("error_code")
        description = str(data.get("description", ""))[:300]  # provider text, no secrets
        retry_after = None
        parameters = data.get("parameters")
        if isinstance(parameters, dict):
            raw = parameters.get("retry_after")
            if isinstance(raw, (int, float)):
                retry_after = max(0, int(raw))
        return TelegramMessageResult(
            outcome=OUTCOME_REJECTED,
            error_code=ERR_API_REJECTED,
            error_message=description or "Telegram API rejected the request",
            http_status=http_status,
            telegram_error_code=tg_error_code if isinstance(tg_error_code, int) else None,
            retry_after=retry_after,
        )

    @staticmethod
    def _extract_message_id(result_payload) -> Optional[int]:
        if not isinstance(result_payload, dict):
            return None
        message_id = result_payload.get("message_id")
        return message_id if isinstance(message_id, int) else None
