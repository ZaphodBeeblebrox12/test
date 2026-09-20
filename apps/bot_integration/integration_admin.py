"""
Admin-side integration tooling for Provision Contract v1.

- BotRuntimeStateAdmin: runtime health in Django Admin.
- VERIFY BOT: read-only signed probe of /provision/version.
- VERIFY CONTROL CHANNEL / VERIFY TARGET CHANNEL: read-only channel checks.
- TEST GRANT / TEST REVOKE: explicit, superuser-only, real operations through
  the same Provision v1 path as production entitlement reconciliation.

Wire-up (two one-line additions — see INTEGRATION_PATCHES.md):
    apps/bot_integration/apps.py  -> ready():  from . import integration_admin
    config/urls.py                -> path("bot-admin/", include("apps.bot_integration.integration_admin"))
"""

from __future__ import annotations

import json

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path

from .models import BotAccessAudit, PlanChannelMapping, TelegramAccount
from .runtime_models import BotRuntimeState
from .services.provision_client import ProvisionClient


# ───────────────────────── Django Admin model ─────────────────────────
@admin.register(BotRuntimeState)
class BotRuntimeStateAdmin(admin.ModelAdmin):
    list_display = ("instance_id", "status", "contract", "contract_version",
                    "base_url", "bot_username", "last_seen")
    list_filter = ("status", "contract", "contract_version")
    readonly_fields = ("instance_id", "base_url", "contract", "contract_version",
                       "operations", "status", "bot_telegram_id", "bot_username",
                       "last_seen", "created_at", "updated_at")
    search_fields = ("instance_id", "base_url", "bot_username")

    def has_add_permission(self, request):
        return False  # runtime state is created by bot self-registration only


# ───────────────────────── shared helpers ─────────────────────────
def _client() -> ProvisionClient:
    return ProvisionClient()


def _control_channel_id() -> str:
    from django.conf import settings
    return str(getattr(settings, "PROVISION_CONTROL_CHANNEL_ID", "") or "").strip()


def _is_control_channel(channel_id: str) -> bool:
    return bool(_control_channel_id()) and str(channel_id).strip() == _control_channel_id()


def _assert_not_control(channel_id: str) -> str | None:
    if _is_control_channel(channel_id):
        return "This is the CONTROL channel — subscriber provisioning targets it is refused."
    return None


# ───────────────────────── VERIFY BOT (read-only) ─────────────────────────
@staff_member_required
def verify_bot(request):
    info = _client().check_health()
    return render(request, "bot_integration/verify_bot.html", {"info": info})


# ───────────────────────── VERIFY CHANNELS (read-only) ─────────────────────────
@staff_member_required
def verify_control_channel(request):
    result = error = None
    channel_id = _control_channel_id()
    if not channel_id:
        error = "PROVISION_CONTROL_CHANNEL_ID / TELEGRAM_CONTROL_CHANNEL_ID is not configured."
    else:
        result = _client().verify_channel(channel_id)
    return render(request, "bot_integration/verify_channel.html", {
        "title": "Verify Control Channel", "channel_id": channel_id,
        "result": result, "error": error, "is_control": True})


@staff_member_required
def verify_target_channel(request, mapping_id: int):
    mapping = get_object_or_404(PlanChannelMapping, pk=mapping_id)
    result = _client().verify_channel(mapping.external_id)
    return render(request, "bot_integration/verify_channel.html", {
        "title": f"Verify Target Channel ({mapping})", "channel_id": mapping.external_id,
        "result": result, "is_control": _is_control_channel(mapping.external_id)})


# ───────────────────────── TEST GRANT / REVOKE (mutating, superuser only) ─────────────────────────
@user_passes_test(lambda u: u.is_active and u.is_superuser)
def test_grant_revoke(request):
    accounts = TelegramAccount.objects.select_related("user").filter(telegram_user_id__isnull=False)
    mappings = PlanChannelMapping.objects.filter(platform="telegram")
    result = None

    if request.method == "POST":
        account = get_object_or_404(TelegramAccount, pk=request.POST.get("account_id"))
        mapping = get_object_or_404(PlanChannelMapping, pk=request.POST.get("mapping_id"))
        operation = request.POST.get("operation")
        if operation not in ("grant", "revoke"):
            return HttpResponseForbidden("Invalid operation")
        guard = _assert_not_control(mapping.external_id)
        if guard:
            messages.error(request, guard)
        else:
            client = _client()
            fn = client.grant if operation == "grant" else client.revoke
            res = fn(account.telegram_user_id, mapping.external_id,
                     idempotency_key=f"admin-test:{operation}:{account.pk}:{mapping.pk}")
            result = res
            BotAccessAudit.objects.create(
                user=account.user, action=f"admin_test_{operation}",
                platform="telegram", external_id=mapping.external_id,
                success=res.ok, error="" if res.ok else res.error_message[:500],
            )
            messages.success(request, f"{operation} -> {res.status or res.error_code}")

    return render(request, "bot_integration/test_grant_revoke.html", {
        "accounts": accounts, "mappings": mappings, "result": result,
        "control_channel_id": _control_channel_id()})


# ───────────────────────── URL module ─────────────────────────
urlpatterns = [
    path("verify-bot/", verify_bot, name="bi_verify_bot"),
    path("verify-control-channel/", verify_control_channel, name="bi_verify_control"),
    path("verify-target-channel/<int:mapping_id>/", verify_target_channel, name="bi_verify_target"),
    path("test-grant-revoke/", test_grant_revoke, name="bi_test_grant_revoke"),
]
