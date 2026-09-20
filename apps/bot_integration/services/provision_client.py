"""
Provision Contract v1 client — the ONLY transport Django uses to reach the
Telegram bridge.  Keeps HTTP details out of reconcile.py.

Endpoint resolution order:
  1. FRESH + COMPATIBLE BotRuntimeState registration  (normal operation)
  2. settings.PROVISION_BOT_URL bootstrap fallback    (first boot only)
  3. BotOffline (retryable)                           (nothing available)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from django.conf import settings

from ..runtime_models import BotRuntimeState


class BotOffline(Exception):
    """No compatible registered bot and no fallback configured."""


@dataclass
class ProvisionResult:
    ok: bool
    status: str = ""                 # applied | already_applied | failed
    retryable: bool = False
    error_code: str = ""
    error_message: str = ""
    source: str = ""                 # registered | fallback
    detail: dict = None

    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


class ProvisionClient:
    def __init__(self, timeout: float = None):
        self.timeout = timeout or getattr(settings, "PROVISION_TIMEOUT", 10.0)
        self._secret = (getattr(settings, "PROVISION_SHARED_SECRET", "") or "")

    # ---- endpoint resolution ------------------------------------------
    def _resolve(self):
        freshness = getattr(settings, "PROVISION_FRESHNESS_SECONDS", 90)
        state = BotRuntimeState.latest_compatible(freshness)
        if state is not None:
            return state.base_url.rstrip("/"), "registered"
        fallback = (getattr(settings, "PROVISION_BOT_URL", "") or "").strip()
        if fallback:
            return fallback.rstrip("/"), "fallback"
        raise BotOffline("no compatible registered bot bridge and no PROVISION_BOT_URL")

    def resolve_endpoint(self):
        """Public: used by admin diagnostics to show which endpoint is active."""
        try:
            return self._resolve()
        except BotOffline:
            return None, "none"

    # ---- signing --------------------------------------------------------
    def _signed_headers(self, body: bytes) -> dict:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        mac = hmac.new(self._secret.encode(), digestmod=hashlib.sha256)
        mac.update(f"{ts}.{nonce}.".encode())
        mac.update(body)
        return {"X-Bot-Instance": "django", "X-Bot-Timestamp": ts,
                "X-Bot-Nonce": nonce, "X-Bot-Signature": mac.hexdigest(),
                "Content-Type": "application/json"}

    def _post(self, path: str, payload: dict) -> ProvisionResult:
        try:
            base_url, source = self._resolve()
        except BotOffline as exc:
            return ProvisionResult(ok=False, status="failed", retryable=True,
                                   error_code="bot_offline", error_message=str(exc))
        body = json.dumps(payload).encode()
        url = f"{base_url}{path}"
        try:
            resp = requests.post(url, data=body, headers=self._signed_headers(body),
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            return ProvisionResult(ok=False, status="failed", retryable=True,
                                   error_code="transport_error", error_message=str(exc),
                                   source=source)
        try:
            data = resp.json()
        except ValueError:
            return ProvisionResult(ok=False, status="failed", retryable=True,
                                   error_code="malformed_response",
                                   error_message=f"HTTP {resp.status_code}: non-JSON body",
                                   source=source)
        if resp.status_code == 200 and data.get("status") in ("applied", "already_applied"):
            return ProvisionResult(ok=True, status=data["status"], source=source,
                                   detail=data.get("detail", {}))
        # Honor HTTP status: a 4xx with retryable=False (e.g. control_channel
        # 403) must stay non-retryable rather than falling to generic retry.
        if resp.status_code in (400, 401, 403, 404, 409):
            return ProvisionResult(ok=False, status="failed",
                                   retryable=bool(data.get("retryable", False)),
                                   error_code=data.get("error_code", "unknown"),
                                   error_message=json.dumps(data.get("detail", data))[:500],
                                   source=source)
        return ProvisionResult(ok=False, status="failed",
                               retryable=bool(data.get("retryable", True)),
                               error_code=data.get("error_code", "unknown"),
                               error_message=json.dumps(data.get("detail", data))[:500],
                               source=source)

    # ---- contract operations --------------------------------------------
    def grant(self, telegram_user_id, channel_id, idempotency_key=None) -> ProvisionResult:
        return self._post("/provision/v1/access", {
            "request_id": str(uuid.uuid4()),
            "operation": "grant",
            "telegram_user_id": int(telegram_user_id),
            "channel_id": str(channel_id),
            "idempotency_key": idempotency_key
                              or f"grant:{telegram_user_id}:{channel_id}",
        })

    def revoke(self, telegram_user_id, channel_id, idempotency_key=None) -> ProvisionResult:
        return self._post("/provision/v1/access", {
            "request_id": str(uuid.uuid4()),
            "operation": "revoke",
            "telegram_user_id": int(telegram_user_id),
            "channel_id": str(channel_id),
            "idempotency_key": idempotency_key
                              or f"revoke:{telegram_user_id}:{channel_id}",
        })

    def verify_channel(self, channel_id) -> ProvisionResult:
        return self._post("/provision/v1/verify-channel", {"channel_id": str(channel_id)})

    def check_membership(self, telegram_user_id, channel_id) -> ProvisionResult:
        """Read-only membership probe (Provision Contract v1).

        Uses the same endpoint resolution, HMAC signing, timeout and
        retryable/non-retryable conventions as _post.  The bridge answers
        status "ok" with a boolean member flag; errors keep their retryable
        flag.  Never creates an invite, never sends a message.
        """
        try:
            base_url, source = self._resolve()
        except BotOffline as exc:
            return ProvisionResult(ok=False, status="failed", retryable=True,
                                   error_code="bot_offline", error_message=str(exc))
        body = json.dumps({
            "request_id": str(uuid.uuid4()),
            "telegram_user_id": int(telegram_user_id),
            "channel_id": str(channel_id),
        }).encode()
        url = f"{base_url}/provision/v1/check-membership"
        try:
            resp = requests.post(url, data=body, headers=self._signed_headers(body),
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            return ProvisionResult(ok=False, status="failed", retryable=True,
                                   error_code="transport_error", error_message=str(exc),
                                   source=source)
        try:
            data = resp.json()
        except ValueError:
            return ProvisionResult(ok=False, status="failed", retryable=True,
                                   error_code="malformed_response",
                                   error_message=f"HTTP {resp.status_code}: non-JSON body",
                                   source=source)
        if resp.status_code == 200 and data.get("status") == "ok":
            return ProvisionResult(ok=True, status="ok", source=source,
                                   detail={"member": bool(data.get("member"))})
        if resp.status_code in (400, 401, 403, 404, 409):
            return ProvisionResult(ok=False, status="failed",
                                   retryable=bool(data.get("retryable", False)),
                                   error_code=data.get("error_code", "unknown"),
                                   error_message=json.dumps(data.get("detail", data))[:500],
                                   source=source)
        return ProvisionResult(ok=False, status="failed",
                               retryable=bool(data.get("retryable", True)),
                               error_code=data.get("error_code", "unknown"),
                               error_message=json.dumps(data.get("detail", data))[:500],
                               source=source)

    def resend_invite(self, telegram_user_id, channel_id,
                      invite_link) -> ProvisionResult:
        """Re-deliver a PERSISTED invite link via the bot's DM path.

        Reuses the existing /provision/v1/access contract (operation
        "resend"): same auth, endpoint resolution, channel semantics, and
        retryable/error conventions as grant/revoke.  Never mints a new
        invite.
        """
        return self._post("/provision/v1/access", {
            "operation": "resend",
            "telegram_user_id": int(telegram_user_id),
            "channel_id": str(channel_id),
            "invite_link": str(invite_link),
        })

    def check_health(self) -> dict:
        """Admin 'Verify Bot' — signed version probe + endpoint metadata."""
        base_url, source = self.resolve_endpoint()
        if not base_url:
            return {"ok": False, "error": "bot_offline", "source": source}
        body = b"{}"
        started = time.time()
        try:
            resp = requests.get(f"{base_url}/provision/version", timeout=self.timeout)
            latency_ms = round((time.time() - started) * 1000, 1)
            data = resp.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc), "source": source, "base_url": base_url}
        return {
            "ok": resp.status_code == 200,
            "source": source,
            "base_url": base_url,
            "latency_ms": latency_ms,
            "contract": data.get("contract"),
            "version": data.get("version"),
            "operations": data.get("operations"),
            "instance_id": data.get("instance_id"),
            "bot": data.get("bot"),
            "compatible": (data.get("contract") == "provision"
                           and data.get("version") == 1
                           and {"grant", "revoke"}.issubset(set(data.get("operations", [])))),
        }
