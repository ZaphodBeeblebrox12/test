import json
import secrets
import logging
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
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
from .sync import sync_user_channels

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
    """Handle Telegram bot updates (deep link verification)."""
    try:
        data = json.loads(request.body)
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

        TelegramBotService.send_message(chat_id, "✅ Your account is now linked! We'll sync your subscription access shortly.")

        from .tasks import sync_user_channels_task
        sync_user_channels_task.delay(user.id)

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
    resp = requests.post('https://discord.com/api/oauth2/token', data=token_data)
    if resp.status_code != 200:
        messages.error(request, "Failed to exchange Discord code.")
        return redirect('profile')

    access_token = resp.json().get('access_token')
    if not access_token:
        messages.error(request, "No access token from Discord.")
        return redirect('profile')

    user_resp = requests.get(
        'https://discord.com/api/users/@me',
        headers={'Authorization': f'Bearer {access_token}'}
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
    from .tasks import sync_user_channels_task
    sync_user_channels_task.delay(request.user.id)

    return redirect('profile')