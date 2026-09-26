"""Ticket review workflow: approve/reject + user notices.

Access-request approval reuses the existing entitlement stack:
  grant_subscription_by_admin() -> Subscription (is_admin_grant=True)
  enqueue_reconcile()           -> adds the user to the plan's Telegram
                                   channels via PlanChannelMapping.
Billing/technical tickets skip the grant - approval just marks resolved.
"""
import logging

from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.bot_integration.transactional import TransactionalTelegramService
from apps.jobs.enqueue import enqueue_reconcile
from apps.subscriptions.services import grant_subscription_by_admin

logger = logging.getLogger(__name__)


def notify_user(ticket, subject, text):
    """Send notice on the plan's configured channel(s).

    Plan tickets use plan.notice_channel (telegram/email/both); other
    categories default to both. Failures are logged, never raised - a
    notice failure must not roll back the review itself.
    """
    channel = "both"
    if ticket.plan_id:
        channel = ticket.plan.notice_channel
    if channel in ("telegram", "both"):
        try:
            TransactionalTelegramService.send_text(ticket.user, text)
        except Exception:
            logger.exception("ticket notice (telegram) failed ticket=%s", ticket.pk)
    if channel in ("email", "both"):
        try:
            send_mail(subject, text, None, [ticket.user.email])
        except Exception:
            logger.exception("ticket notice (email) failed ticket=%s", ticket.pk)


def send_ticket_confirmation(ticket):
    """Confirm ticket creation to the user on Telegram + email."""
    text = (f"We received your ticket ({ticket.get_category_display()}). "
            "We'll review it and get back to you shortly.")
    try:
        TransactionalTelegramService.send_text(ticket.user, text)
    except Exception:
        logger.exception("ticket confirmation (telegram) failed ticket=%s", ticket.pk)
    try:
        send_mail(f"Ticket received - {ticket.short_id}", text, None,
                  [ticket.user.email])
    except Exception:
        logger.exception("ticket confirmation (email) failed ticket=%s", ticket.pk)


@transaction.atomic
def approve_ticket(ticket, plan, admin):
    """Approve a ticket.

    Access requests: `plan` is required - the user is granted the hidden
    plan and reconciled into its Telegram channels. Other categories:
    approval marks the ticket resolved (plan may be None).
    """
    from .models import SupportTicket

    if not ticket.can_be_reviewed_by(admin):
        raise PermissionError("You are not allowed to review this ticket.")
    if ticket.status != SupportTicket.Status.PENDING:
        raise ValueError("Only pending tickets can be approved.")

    grant = None
    if ticket.is_access_request:
        if plan is None:
            raise ValueError("Assign a plan before approving an access request.")
        grant = grant_subscription_by_admin(
            ticket.user, plan, granted_by=admin,
            duration_days=plan.grant_duration_days,
            reason=f"Access request {ticket.short_id} approved",
        )
        try:
            enqueue_reconcile(ticket.user_id, reason="support_ticket_approved")
        except Exception:
            logger.exception("reconcile enqueue failed after approval ticket=%s", ticket.pk)

    if ticket.is_access_request:
        ticket.plan = plan
    ticket.status = SupportTicket.Status.APPROVED
    ticket.reviewed_by = admin
    ticket.reviewed_at = timezone.now()
    ticket.save(update_fields=(["plan"] if ticket.is_access_request else [])
                + ["status", "reviewed_by", "reviewed_at", "updated_at"])

    if ticket.is_access_request and plan is not None:
        duration = (f"{plan.grant_duration_days} day(s)" if plan.grant_duration_days
                    else "no expiry")
        notify_user(
            ticket,
            f"Your access request was approved - {plan.name}",
            f"Your request was approved. You now have access to {plan.name} "
            f"({duration}). If a Telegram group is linked to this plan you will "
            f"be added shortly.",
        )
    else:
        notify_user(
            ticket,
            "Your ticket has been resolved",
            f"Your ticket ({ticket.get_category_display()}) has been resolved. "
            f"{ticket.admin_notes}".strip(),
        )
    return grant


@transaction.atomic
def reject_ticket(ticket, admin, notes):
    """Reject/close a pending ticket and notify the user with the reason."""
    from .models import SupportTicket

    if not ticket.can_be_reviewed_by(admin):
        raise PermissionError("You are not allowed to review this ticket.")
    if ticket.status != SupportTicket.Status.PENDING:
        raise ValueError("Only pending tickets can be rejected.")
    if not notes.strip():
        raise ValueError("A reason (admin notes) is required.")

    ticket.status = SupportTicket.Status.REJECTED
    ticket.admin_notes = notes
    ticket.reviewed_by = admin
    ticket.reviewed_at = timezone.now()
    ticket.save(update_fields=["status", "admin_notes", "reviewed_by",
                               "reviewed_at", "updated_at"])

    notify_user(
        ticket,
        "Your ticket was not approved",
        f"Your ticket ({ticket.get_category_display()}) was reviewed and not "
        f"approved. Reason: {notes}",
    )
    return ticket
