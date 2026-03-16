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
