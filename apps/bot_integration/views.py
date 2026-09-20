import json
import secrets
import logging
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from .models import (
    BotConfig, TelegramAccount, TelegramVerificationToken,
    DiscordAccount, BotAccessAudit
)
from .services.telegram import TelegramBotService

logger = logging.getLogger(__name__)


@login_required
def start_telegram_connect(request):
    """Generate a deep link and redirect immediately to Telegram."""
    config = BotConfig.get_config()
    if not config.telegram_bot_username:
        messages.error(request, "Telegram bot is not configured.")
        return redirect('profile')

    # Delete old unused tokens for this user
    TelegramVerificationToken.objects.filter(user=request.user).delete()

    token = TelegramVerificationToken.create_token(request.user)
    deep_link = f"https://t.me/{config.telegram_bot_username}?start=verify_{token.token}"
    
    # Redirect directly to Telegram (no intermediate page)
    return redirect(deep_link)


@login_required
def unlink_telegram(request):
    """Disconnect Telegram account from user profile."""
    try:
        tg = TelegramAccount.objects.get(user=request.user)
        old_chat_id = tg.chat_id
        tg.delete()
        BotAccessAudit.objects.create(
            user=request.user,
            action='unlink',
            platform='telegram',
            target=str(old_chat_id),
            status='success'
        )
        messages.success(request, "Telegram account unlinked.")
    except TelegramAccount.DoesNotExist:
        pass
    return redirect('profile')


@login_required
def unlink_discord(request):
    """Disconnect Discord account from user profile."""
    try:
        dc = DiscordAccount.objects.get(user=request.user)
        old_id = dc.discord_user_id
        dc.delete()
        BotAccessAudit.objects.create(
            user=request.user,
            action='unlink',
            platform='discord',
            target=old_id,
            status='success'
        )
        messages.success(request, "Discord account unlinked.")
    except DiscordAccount.DoesNotExist:
        pass
    return redirect('profile')


@csrf_exempt
@require_POST
def telegram_webhook(request):
    """Handle Telegram bot updates (deep link verification).

    Updates arrive via the signal bot's polling loop, which forwards
    /start verify_<token> updates here over HTTP (Option 1 architecture —
    Django runs on localhost and cannot receive Telegram webhooks directly).
    Requests must carry the Provision Contract v1 HMAC signature; native
    Telegram webhook delivery is not used.
    """
    from .provision_auth import verify_signed_request
    body, auth_err = verify_signed_request(request)
    if auth_err:
        return JsonResponse({"ok": False, "error": auth_err}, status=401)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    logger.info(f"Telegram webhook received: {data}")

    if 'message' not in data or 'text' not in data['message']:
        return JsonResponse({"ok": True})

    text = data['message']['text']
    chat_id = data['message']['chat']['id']
    from_user = data['message'].get('from', {})
    telegram_user_id = from_user.get('id')

    if not text.startswith('/start'):
        return JsonResponse({"ok": True})

    parts = text.split()
    if len(parts) != 2 or not parts[1].startswith('verify_'):
        return JsonResponse({"ok": True})

    token_str = parts[1][7:]  # remove 'verify_'

    try:
        token = TelegramVerificationToken.objects.select_related('user').get(token=token_str)
        if not token.is_valid():
            TelegramBotService.send_message(chat_id, "❌ Verification link expired. Please request a new one on the website.")
            token.delete()
            return JsonResponse({"ok": False})

        user = token.user
        # Transactional outbox: account + audit + job commit atomically.
        from django.db import transaction as _tx
        from apps.jobs.enqueue import enqueue_reconcile
        with _tx.atomic():
            TelegramAccount.objects.update_or_create(
                user=user,
                defaults={
                    'chat_id': chat_id,
                    'telegram_user_id': telegram_user_id,
                    'is_active': True
                }
            )
            token.delete()

            BotAccessAudit.objects.create(
                user=user,
                action='link',
                platform='telegram',
                target=str(chat_id),
                status='success'
            )

            enqueue_reconcile(user.id, reason="telegram_link")

        TelegramBotService.send_message(chat_id, "✅ Your account is now linked! We'll sync your subscription access shortly.")

        return JsonResponse({"ok": True})

    except TelegramVerificationToken.DoesNotExist:
        TelegramBotService.send_message(chat_id, "❌ Invalid verification code.")
        return JsonResponse({"ok": False})


def discord_oauth_start(request):
    """Redirect user to Discord OAuth2 authorization page."""
    config = BotConfig.get_config()
    if not config.discord_client_id or not config.discord_redirect_uri:
        messages.error(request, "Discord OAuth not configured.")
        return redirect('profile')

    state = secrets.token_urlsafe(32)
    request.session['discord_oauth_state'] = state

    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={config.discord_client_id}"
        f"&redirect_uri={config.discord_redirect_uri}"
        f"&response_type=code"
        f"&scope=identify%20guilds"
        f"&state={state}"
    )
    return redirect(auth_url)


def discord_oauth_callback(request):
    """Handle Discord OAuth2 callback and link the user's Discord account."""
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')

    session_state = request.session.pop('discord_oauth_state', None)
    if error or not code or state != session_state:
        messages.error(request, "Discord authentication failed.")
        return redirect('profile')

    config = BotConfig.get_config()
    if not config.discord_client_id or not config.discord_client_secret:
        messages.error(request, "Discord OAuth not configured.")
        return redirect('profile')

    import requests
    token_data = {
        'client_id': config.discord_client_id,
        'client_secret': config.discord_client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': config.discord_redirect_uri,
    }
    resp = requests.post('https://discord.com/api/oauth2/token', data=token_data, timeout=10)
    if resp.status_code != 200:
        messages.error(request, "Failed to exchange Discord code.")
        return redirect('profile')

    access_token = resp.json().get('access_token')
    if not access_token:
        messages.error(request, "No access token from Discord.")
        return redirect('profile')

    user_resp = requests.get(
        'https://discord.com/api/users/@me',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    )
    if user_resp.status_code != 200:
        messages.error(request, "Failed to fetch Discord user info.")
        return redirect('profile')

    user_info = user_resp.json()
    discord_id = user_info.get('id')
    username = user_info.get('username')

    if not discord_id:
        messages.error(request, "No Discord ID received.")
        return redirect('profile')

    if DiscordAccount.objects.filter(discord_user_id=discord_id).exclude(user=request.user).exists():
        messages.error(request, "This Discord account is already linked to another user.")
        return redirect('profile')

    DiscordAccount.objects.update_or_create(
        user=request.user,
        defaults={
            'discord_user_id': discord_id,
            'guild_id': config.discord_guild_id,
            'username': username,
            'is_active': True
        }
    )

    BotAccessAudit.objects.create(
        user=request.user,
        action='link',
        platform='discord',
        target=discord_id,
        status='success'
    )

    messages.success(request, "Discord account linked successfully!")
    from apps.jobs.enqueue import enqueue_reconcile
    enqueue_reconcile(request.user.id, reason="discord_link")

    return redirect('profile')

# ════════════════════════════════════════════════════════════════════════════
# Bot bridge self-registration / heartbeat  (Provision Contract v1)
# HMAC-authenticated API endpoints — NOT user-facing, NOT login-protected;
# authentication is the shared-secret signature itself.
# ════════════════════════════════════════════════════════════════════════════

import json as _json
from urllib.parse import urlsplit as _urlsplit

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .provision_auth import verify_signed_request
from .runtime_models import BotRuntimeState


def _allowed_base_url(url: str) -> bool:
    """Validate the bot-reported URL without becoming an SSRF primitive."""
    try:
        parts = _urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    # Intentionally support local/private deployment (127.0.0.1, LAN IPs,
    # private hostnames).  Only obviously-dangerous targets are refused.
    blocked = {"169.254.169.254", "metadata.google.internal"}
    return parts.hostname not in blocked


@csrf_exempt
@require_POST
def bot_register(request):
    body, err = verify_signed_request(request)
    if err:
        return JsonResponse({"ok": False, "error": err}, status=401)
    try:
        data = _json.loads(body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "malformed JSON"}, status=400)

    instance_id = str(data.get("instance_id") or "").strip()
    base_url = str(data.get("base_url") or "").strip()
    contract = data.get("contract", "")
    version = data.get("version")
    operations = data.get("operations") or []

    if not instance_id:
        return JsonResponse({"ok": False, "error": "instance_id required"}, status=400)
    if not _allowed_base_url(base_url):
        return JsonResponse({"ok": False, "error": "invalid base_url"}, status=400)
    if not isinstance(operations, list):
        return JsonResponse({"ok": False, "error": "operations must be a list"}, status=400)

    bot_info = data.get("bot") if isinstance(data.get("bot"), dict) else {}
    state = BotRuntimeState.touch(
        instance_id,
        base_url=base_url,
        contract=str(contract),
        contract_version=int(version or 0),
        operations=operations,
        bot_telegram_id=bot_info.get("id"),
        bot_username=str(bot_info.get("username") or ""),
        status=(BotRuntimeState.STATUS_ONLINE
                if contract == "provision" and version == 1
                else BotRuntimeState.STATUS_INCOMPATIBLE),
    )
    return JsonResponse({"ok": True, "instance_id": state.instance_id,
                         "status": state.status})


@csrf_exempt
@require_POST
def bot_heartbeat(request):
    body, err = verify_signed_request(request)
    if err:
        return JsonResponse({"ok": False, "error": err}, status=401)
    try:
        data = _json.loads(body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "malformed JSON"}, status=400)

    instance_id = str(data.get("instance_id") or "").strip()
    try:
        state = BotRuntimeState.objects.get(instance_id=instance_id)
    except BotRuntimeState.DoesNotExist:
        # Django lost our state (DB reset etc.) — force the bot to re-register.
        return JsonResponse({"ok": False, "error": "unknown instance"}, status=404)

    updates = {"status": BotRuntimeState.STATUS_ONLINE if state.is_compatible()
               else BotRuntimeState.STATUS_INCOMPATIBLE}
    base_url = str(data.get("base_url") or "").strip()
    if base_url and _allowed_base_url(base_url) and base_url != state.base_url:
        updates["base_url"] = base_url
    for key, value in updates.items():
        setattr(state, key, value)
    # Heartbeat proves liveness: stamp last_seen so the freshness-based
    # endpoint resolution (ProvisionClient) keeps selecting this instance.
    state.last_seen = timezone.now()
    state.save(update_fields=[*updates.keys(), "last_seen", "updated_at"])
    return JsonResponse({"ok": True, "instance_id": state.instance_id})
