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
        # Get data from POST
        data = request.POST.dict()

        # Verify required fields
        required_fields = ['id', 'hash', 'auth_date']
        for field in required_fields:
            if field not in data:
                return HttpResponseBadRequest(f"Missing field: {field}")

        # Verify hash
        bot_token = settings.TELEGRAM_BOT_TOKEN
        if not bot_token:
            return HttpResponseBadRequest("Telegram bot not configured")

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

        if calculated_hash != check_hash:
            return HttpResponseBadRequest("Invalid hash")

        # Check auth_date is recent (within 24 hours)
        auth_date = int(data['auth_date'])
        current_time = int(time.time())
        if current_time - auth_date > 86400:
            return HttpResponseBadRequest("Auth date too old")

        # Get or create user
        telegram_id = int(data['id'])
        telegram_username = data.get('username', '')
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')

        try:
            user = User.objects.get(telegram_id=telegram_id)
            # Update user info
            user.telegram_username = telegram_username
            user.telegram_verified = True
            user.first_name = first_name or user.first_name
            user.last_name = last_name or user.last_name
            user.save()

            # Log login
            AuditLog.log(
                action="login_telegram",
                user=user,
                object_type="user",
                object_id=user.id,
                metadata={"telegram_id": telegram_id, "telegram_username": telegram_username}
            )
        except User.DoesNotExist:
            # Create new user
            base_username = telegram_username or f"tg_{telegram_id}"
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}_{counter}"
                counter += 1

            user = User.objects.create(
                username=username,
                telegram_id=telegram_id,
                telegram_username=telegram_username,
                telegram_verified=True,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
            )

            # Create user preferences
            from apps.accounts.models import UserPreference
            UserPreference.objects.get_or_create(user=user)

            # Log signup
            AuditLog.log(
                action="signup_telegram",
                user=user,
                object_type="user",
                object_id=user.id,
                metadata={"telegram_id": telegram_id, "telegram_username": telegram_username}
            )

        # Login user
        login(request, user)

        return redirect('dashboard')
