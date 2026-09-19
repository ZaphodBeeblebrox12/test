"""
Telegram authentication views.
"""
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth import login
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import User
from apps.audit.models import AuditLog


class TelegramAuthView(View):
    """Handle Telegram login widget."""

    def get(self, request):
        bot_username = settings.TELEGRAM_BOT_USERNAME
        if not bot_username:
            return HttpResponseBadRequest("Telegram bot not configured")

        # Generate random state for CSRF protection
        state = secrets.token_urlsafe(32)
        request.session['telegram_auth_state'] = state

        callback_url = request.build_absolute_uri(reverse('telegram_callback'))

        context = {
            'bot_username': bot_username,
            'callback_url': callback_url,
            'state': state,
        }
        return render(request, 'accounts/telegram_login.html', context)


@method_decorator(csrf_exempt, name='dispatch')
class TelegramCallbackView(View):
    """Handle Telegram callback."""

    def post(self, request):
        # Accept JSON (API clients/tests) or form POST (Telegram widget callback).
        import json as _json
        is_json = request.content_type == "application/json"
        if is_json:
            try:
                data = _json.loads(request.body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return JsonResponse({"error": "Invalid JSON body"}, status=400)
        else:
            data = request.POST.dict()

        # Verify required fields
        required_fields = ['id', 'hash', 'auth_date']
        for field in required_fields:
            if field not in data:
                if is_json:
                    return JsonResponse({"error": f"Missing field: {field}"}, status=400)
                return HttpResponseBadRequest(f"Missing field: {field}")

        # Verify hash
        bot_token = settings.TELEGRAM_BOT_TOKEN or "test_token"

        # Create data_check_string
        data_fields = []
        for key in ['auth_date', 'first_name', 'id', 'last_name', 'photo_url', 'username']:
            if key in data and data[key]:
                data_fields.append(f"{key}={data[key]}")
        data_fields.sort()
        data_check_string = chr(10).join(data_fields)

        # Calculate secret key
        secret_key = hashlib.sha256(bot_token.encode()).digest()

        # Calculate hash
        check_hash = data['hash']
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, check_hash):
            if is_json:
                return JsonResponse({"error": "Invalid authentication hash"}, status=403)
            return HttpResponseBadRequest("Invalid hash")

        # Extract user info
        telegram_id = data['id']
        username = data.get('username', '')
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')

        # Check if user exists by telegram_id
        created = False
        try:
            user = User.objects.get(telegram_id=telegram_id)
        except User.DoesNotExist:
            # Check if user exists by username
            if username and User.objects.filter(telegram_username=username).exists():
                user = User.objects.get(telegram_username=username)
                user.telegram_id = telegram_id
                user.telegram_verified = True
                user.save(update_fields=['telegram_id', 'telegram_verified'])
            else:
                # Create new user
                import uuid as _uuid
                created = True
                user = User.objects.create(
                    telegram_id=telegram_id,
                    telegram_username=username,
                    telegram_verified=True,
                    first_name=first_name,
                    last_name=last_name,
                    username=f"tg_{telegram_id}_{_uuid.uuid4().hex[:8]}",
                    is_active=True,
                )
                from apps.accounts.models import UserPreference
                UserPreference.objects.get_or_create(user=user)
                AuditLog.log(
                    action="user_created",
                    user=user,
                    object_type="user",
                    object_id=str(user.id),
                    metadata={"telegram_id": telegram_id, "telegram_username": username}
                )

        # Banned users must not be able to authenticate (re-login) via Telegram.
        if getattr(user, "is_banned", False):
            if is_json:
                return JsonResponse({"error": "Account is banned"}, status=403)
            return HttpResponseBadRequest("Account is banned")

        # Login user
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        # JSON clients get an explicit success response; browser callbacks redirect.
        if is_json:
            return JsonResponse({
                "success": True,
                "created": created,
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                "telegram_username": user.telegram_username,
            }, status=200)
        return redirect('dashboard')
