"""
Notifications service for email delivery.

This module provides the interface for sending emails and tracking delivery.
Other apps (like growth) should use this instead of sending email directly.
"""
import logging
from typing import Dict, Any, Optional

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib.sites.models import Site
from django.utils import timezone

from .models import EmailLog

logger = logging.getLogger(__name__)


class NotificationServiceError(Exception):
    """Base exception for notification errors."""
    pass


class NotificationService:
    """
    Service for sending notifications and emails.

    This is the central point for all email delivery in the application.
    Apps should never send email directly using django.core.mail.
    """

    @classmethod
    def send_email(
        cls,
        to_email: str,
        template: str,
        subject: str,
        context: Dict[str, Any],
        from_email: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send an email using a template.

        Args:
            to_email: Recipient email address
            template: Template path (e.g., 'growth/gift_invite')
            subject: Email subject
            context: Template context dictionary
            from_email: Sender email (defaults to DEFAULT_FROM_EMAIL)
            metadata: Additional metadata for tracking

        Returns:
            True if email was sent successfully

        Raises:
            NotificationServiceError: If email fails to send
        """
        from_email = from_email or settings.DEFAULT_FROM_EMAIL

        # Add site to context
        try:
            site = Site.objects.get_current()
            context['site'] = site
            context['site_name'] = site.name
        except Site.DoesNotExist:
            context['site_name'] = 'Community Platform'

        # Render templates
        try:
            # Subject template (optional override)
            subject_template = f"{template}_subject.txt"
            try:
                subject = render_to_string(subject_template, context).strip()
            except Exception:
                # Use provided subject if template doesn't exist
                pass

            # Body templates
            text_body = render_to_string(f"{template}_body.txt", context)

            # Try HTML version
            html_body = None
            try:
                html_body = render_to_string(f"{template}_body.html", context)
            except Exception:
                pass  # HTML is optional

        except Exception as e:
            logger.error(f"Failed to render email templates for {template}: {e}")
            raise NotificationServiceError(f"Template rendering failed: {e}")

        # Create email log entry
        email_log = EmailLog.objects.create(
            email=to_email,
            template=template,
            subject=subject,
            status="queued",
            metadata=metadata or {}
        )

        try:
            # Send the email
            if html_body:
                send_mail(
                    subject=subject,
                    message=text_body,
                    from_email=from_email,
                    recipient_list=[to_email],
                    html_message=html_body,
                    fail_silently=False,
                )
            else:
                send_mail(
                    subject=subject,
                    message=text_body,
                    from_email=from_email,
                    recipient_list=[to_email],
                    fail_silently=False,
                )

            # Update log
            email_log.status = "sent"
            email_log.sent_at = timezone.now()
            email_log.save(update_fields=['status', 'sent_at'])

            logger.info(f"Email sent to {to_email} using template {template}")
            return True

        except Exception as e:
            # Update log with error
            email_log.status = "failed"
            email_log.error_message = str(e)
            email_log.save(update_fields=['status', 'error_message'])

            logger.error(f"Failed to send email to {to_email}: {e}")
            raise NotificationServiceError(f"Email sending failed: {e}")

    @classmethod
    def send_simple_email(
        cls,
        to_email: str,
        subject: str,
        body: str,
        from_email: Optional[str] = None,
        html_body: Optional[str] = None,
    ) -> bool:
        """
        Send a simple email without templates.

        Args:
            to_email: Recipient email address
            subject: Email subject
            body: Plain text body
            from_email: Sender email
            html_body: Optional HTML body

        Returns:
            True if sent successfully
        """
        from_email = from_email or settings.DEFAULT_FROM_EMAIL

        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=from_email,
                recipient_list=[to_email],
                html_message=html_body,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send simple email to {to_email}: {e}")
            return False
