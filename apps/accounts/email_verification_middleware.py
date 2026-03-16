"""
Email Verification Middleware

Ensures that users with unverified email addresses cannot access protected pages.
Uses allauth's built-in email verification checking.
"""
from django.conf import settings
from django.shortcuts import redirect
from django.urls import resolve, reverse, NoReverseMatch


class EmailVerificationMiddleware:
    """
    Middleware to enforce email verification for protected routes.

    Unverified users are redirected to the email verification sent page.
    Google users are automatically verified (handled by allauth SOCIALACCOUNT_EMAIL_VERIFICATION).
    Telegram users may not have email initially, so they are exempt.
    """

    # URLs that unverified users CAN access
    ALLOWED_URLS = [
        '/admin/',
        '/accounts/',
        '/auth/',
        '/api/',
        '/static/',
        '/media/',
        '/login',
        '/logout',
        '/signup',
        '/verify-email',
        '/confirm-email',
        '/password',
        '/telegram/',
        '/auth/telegram/',
    ]

    # URL names that are allowed
    ALLOWED_URL_NAMES = [
        'account_login',
        'account_logout',
        'account_signup',
        'account_email_verification_sent',
        'account_confirm_email',
        'account_email',
        'account_change_password',
        'account_set_password',
        'account_reset_password',
        'account_reset_password_done',
        'account_reset_password_from_key',
        'account_reset_password_from_key_done',
        'google_login',
        'google_callback',
        'telegram_login',
        'telegram_callback',
        'telegram_verify',
        'telegram_connect',
        'index',
        'home',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if user is authenticated
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Check if user is banned
        if hasattr(request.user, 'is_banned') and request.user.is_banned:
            return self.get_response(request)

        # Check if this is an allowed URL
        if self._is_allowed_url(request):
            return self.get_response(request)

        # Check if user needs email verification
        if self._requires_verification(request.user):
            # Redirect to verification sent page
            try:
                return redirect('account_email_verification_sent')
            except NoReverseMatch:
                # Fallback to home if URL not found
                return redirect('/')

        return self.get_response(request)

    def _is_allowed_url(self, request):
        """Check if the current URL is in the allowed list."""
        path = request.path

        # Check exact path matches
        for allowed in self.ALLOWED_URLS:
            if path.startswith(allowed):
                return True

        # Check URL name
        try:
            resolver = resolve(path)
            url_name = resolver.url_name
            if url_name in self.ALLOWED_URL_NAMES:
                return True
        except:
            pass

        return False

    def _requires_verification(self, user):
        """
        Check if user requires email verification.

        Exemptions:
        - Superusers and staff
        - Users with no email (Telegram users)
        - Users who signed up via social auth (Google)
        - Users with verified email
        """
        # Superusers and staff are exempt
        if user.is_superuser or user.is_staff:
            return False

        # Users without email (Telegram users) are exempt
        if not user.email:
            return False

        # Check if email is verified via allauth
        try:
            from allauth.account.models import EmailAddress
            email_address = EmailAddress.objects.filter(user=user, email=user.email).first()
            if email_address and email_address.verified:
                return False
        except:
            pass

        # Check if user has any verified emails
        try:
            from allauth.account.models import EmailAddress
            has_verified = EmailAddress.objects.filter(user=user, verified=True).exists()
            if has_verified:
                return False
        except:
            pass

        return True
