"""
Signal handlers for email verification and user management.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed, user_signed_up

from apps.accounts.models import User


@receiver(email_confirmed)
def on_email_confirmed(request, email_address, **kwargs):
    """
    Handle email confirmation.
    Log the verification event.
    """
    user = email_address.user

    # Log the email verification
    from apps.audit.models import AuditLog
    AuditLog.log(
        action="email_verified",
        user=user,
        object_type="user",
        object_id=user.id,
        metadata={"email": email_address.email}
    )

    # Chargeback evidence trail: email verification is anti-ATO evidence.
    _security_event(request, user, "email_verified")


@receiver(user_signed_up)
def on_user_signed_up(request, user, **kwargs):
    """
    Handle user signup via email.
    Ensure email address record is created for verification.
    """
    if user.email:
        # Create EmailAddress record if it doesn't exist
        EmailAddress.objects.get_or_create(
            user=user,
            email=user.email,
            defaults={'primary': True, 'verified': False}
        )

        # Log the signup
        from apps.audit.models import AuditLog
        AuditLog.log(
            action="signup_email",
            user=user,
            object_type="user",
            object_id=user.id,
            metadata={"email": user.email}
        )


# --- Chargeback evidence: append-only security trail -----------------------
def _security_event(request, user, event_type, detail=None):
    """Best-effort append-only security record; never blocks the request."""
    try:
        from apps.accounts.models import SecurityEvent
        ip = ""
        ua = ""
        if request is not None:
            ip = (request.META.get("HTTP_X_FORWARDED_FOR", "")
                  .split(",")[0].strip()
                  or request.META.get("REMOTE_ADDR") or "")
            ua = request.META.get("HTTP_USER_AGENT", "")[:2000]
        SecurityEvent.objects.create(
            user=user, event_type=event_type, ip_address=ip or "0.0.0.0",
            user_agent=ua, detail=detail or {})
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "security event write failed (%s)", event_type)


def record_login(request, user, success, detail=None):
    """Called from the login flow (apps.accounts views / adapter) to build
    the login-continuity evidence timeline used in dispute cases."""
    _security_event(request, user,
                    "login_ok" if success else "login_failed", detail)


def record_password_change(request, user):
    """ATO defense: password changes near disputed purchases matter."""
    _security_event(request, user, "password_changed")


# Login trail wiring: allauth/django auth signals -> SecurityEvent.
# user_logged_in carries the request; user_login_failed only carries the
# attempted credentials (we resolve the user to attribute the failure).
from django.contrib.auth.signals import user_logged_in, user_login_failed


@receiver(user_logged_in)
def on_logged_in(sender, request, user, **kwargs):
    record_login(request, user, True)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request, **kwargs):
    username = (credentials or {}).get("username") or ""
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return  # unknown user: no account to attribute the failure to
    record_login(request, user, False,
                 detail={"reason": "invalid_credentials"})
