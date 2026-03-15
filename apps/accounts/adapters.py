"""
Custom allauth adapter for Google authentication.
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.accounts.models import User


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom adapter to handle Google login and prevent duplicate emails."""

    def pre_social_login(self, request, sociallogin):
        """
        Handle pre-social login.
        Check if user with this email already exists and connect accounts.
        """
        email = sociallogin.user.email
        if not email:
            return

        try:
            existing_user = User.objects.get(email=email)
            # If user exists with this email, connect the social account
            if not sociallogin.is_existing:
                sociallogin.connect(request, existing_user)
                # Log the login
                from apps.audit.models import AuditLog
                AuditLog.log(
                    action="login_google",
                    user=existing_user,
                    object_type="user",
                    object_id=existing_user.id,
                    metadata={"provider": "google", "email": email}
                )
        except User.DoesNotExist:
            pass

    def save_user(self, request, sociallogin, form=None):
        """
        Save user with telegram_verified=False for Google users.
        """
        user = super().save_user(request, sociallogin, form)

        # Set telegram_verified to False for Google login
        user.telegram_verified = False

        # Generate username from email if not set
        if not user.username:
            base_username = user.email.split('@')[0] if user.email else "user"
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1
            user.username = username

        user.save()

        # Create user preferences
        from apps.accounts.models import UserPreference
        UserPreference.objects.get_or_create(user=user)

        # Log the signup
        from apps.audit.models import AuditLog
        AuditLog.log(
            action="signup_google",
            user=user,
            object_type="user",
            object_id=user.id,
            metadata={"provider": "google", "email": user.email}
        )

        return user


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom account adapter."""

    def get_login_redirect_url(self, request):
        return "/dashboard/"

    def get_logout_redirect_url(self, request):
        return "/"
