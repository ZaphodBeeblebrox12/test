"""
Discord authentication views for account verification.
"""
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.services.discord_service import DiscordOAuth2Service
from apps.audit.models import AuditLog


def check_banned(view_func):
    """Decorator to check if user is banned."""
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_banned:
            return render(request, "accounts/banned.html", {
                "ban_reason": request.user.ban_reason
            })
        return view_func(request, *args, **kwargs)
    return wrapper


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class DiscordConnectView(View):
    """Initiate Discord OAuth2 connection flow."""

    def get(self, request):
        """Redirect user to Discord OAuth2 authorization page."""
        try:
            # Generate state for CSRF protection
            state = secrets.token_urlsafe(32)
            request.session['discord_oauth_state'] = state

            auth_url = DiscordOAuth2Service.get_authorization_url(state=state)
            return HttpResponseRedirect(auth_url)
        except Exception as e:
            return render(request, "accounts/discord_error.html", {
                "error": str(e),
                "error_title": "Discord Configuration Error"
            })


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class DiscordCallbackView(View):
    """Handle Discord OAuth2 callback."""

    def get(self, request):
        """Process OAuth2 callback from Discord."""
        code = request.GET.get('code')
        state = request.GET.get('state')
        error = request.GET.get('error')

        # Check for OAuth errors
        if error:
            return render(request, "accounts/discord_error.html", {
                "error": f"Discord authorization failed: {error}",
                "error_title": "Authorization Failed"
            })

        # Verify state parameter for CSRF protection
        session_state = request.session.get('discord_oauth_state')
        if not state or state != session_state:
            return render(request, "accounts/discord_error.html", {
                "error": "Invalid state parameter. Please try again.",
                "error_title": "Security Error"
            })

        # Clear state from session
        if 'discord_oauth_state' in request.session:
            del request.session['discord_oauth_state']

        if not code:
            return render(request, "accounts/discord_error.html", {
                "error": "No authorization code received from Discord.",
                "error_title": "Authorization Failed"
            })

        try:
            # Exchange code for access token
            token_data = DiscordOAuth2Service.exchange_code_for_token(code)
            access_token = token_data.get('access_token')

            if not access_token:
                raise Exception("No access token received from Discord")

            # Get user info from Discord
            user_info = DiscordOAuth2Service.get_user_info(access_token)

            discord_id = user_info.get('id')
            discord_username = user_info.get('username')
            discord_avatar = user_info.get('avatar')

            if not discord_id:
                raise Exception("No Discord ID received")

            # Check if Discord ID is already connected to another user
            existing_user = User.objects.filter(
                discord_id=discord_id
            ).exclude(id=request.user.id).first()

            if existing_user:
                return render(request, "accounts/discord_error.html", {
                    "error": "This Discord account is already connected to another user.",
                    "error_title": "Account Already Connected"
                })

            # Store Discord info on current user
            user = request.user
            user.discord_id = discord_id
            user.discord_username = discord_username
            user.discord_avatar = discord_avatar
            user.discord_verified = True
            user.save()

            # Log the connection
            AuditLog.log(
                action="discord_connected",
                user=user,
                object_type="user",
                object_id=user.id,
                metadata={
                    "discord_id": discord_id,
                    "discord_username": discord_username
                }
            )

            # Redirect to profile page with success
            return redirect('profile')

        except Exception as e:
            return render(request, "accounts/discord_error.html", {
                "error": str(e),
                "error_title": "Discord Connection Failed"
            })


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class DiscordDisconnectView(View):
    """Disconnect Discord account from user profile."""

    def post(self, request):
        """Remove Discord connection from user account."""
        user = request.user

        if user.discord_id:
            # Log the disconnection
            AuditLog.log(
                action="discord_disconnected",
                user=user,
                object_type="user",
                object_id=user.id,
                metadata={
                    "discord_id": str(user.discord_id),
                    "discord_username": user.discord_username
                }
            )

            # Clear Discord fields
            user.discord_id = None
            user.discord_username = ""
            user.discord_avatar = ""
            user.discord_verified = False
            user.save()

        return redirect('profile')


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def discord_connect_api(request):
    """
    API endpoint to initiate Discord OAuth flow.
    Returns the authorization URL for the frontend to redirect to.
    """
    try:
        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)
        request.session['discord_oauth_state'] = state

        auth_url = DiscordOAuth2Service.get_authorization_url(state=state)

        return Response({
            "authorization_url": auth_url
        })
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
