"""Support tickets: access requests (referral verification) + billing and
technical issues.

Access-request flow: a user raises a SupportTicket (category=access_request)
picking the AffiliateLink they signed up through, with screenshot
attachments proving the account + trades. An approver reviews and either
approves (the mapped hidden plan is granted -> the user is reconciled into
the plan's Telegram channels) or rejects (user is notified on the plan's
configured notice_channel).

Billing/technical tickets use the same queue but skip the grant flow —
approval simply marks the ticket resolved and notifies the user.
"""
import os
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.subscriptions.models import Plan

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_ATTACHMENTS_PER_TICKET = 5
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB


def ticket_attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"support/tickets/{instance.ticket_id}/{uuid.uuid4()}{ext}"


class AffiliateLink(models.Model):
    """One of the owner's referral/affiliate links (per broker or code)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=100,
        help_text=_('Label shown to users, e.g. "Exness" or "IC Markets VIP"'))
    url = models.URLField(help_text=_("Your referral link for this broker/code"))
    referral_code = models.CharField(
        max_length=100,
        help_text=_("The code the broker gave you - used to verify screenshots"))
    plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True,
        limit_choices_to={"is_hidden": True},
        related_name="affiliate_links",
        help_text=_("Optional: hidden plan auto-assigned when an access request "
                    "using this link is approved (you can still override)"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SupportTicket(models.Model):
    """A user-raised ticket: access request, billing issue, or technical issue."""

    class Category(models.TextChoices):
        ACCESS = "access_request", _("Access request (referral verification)")
        BILLING = "billing", _("Billing / payment issue")
        TECHNICAL = "technical", _("Technical issue (Telegram access, etc.)")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved / Resolved")
        REJECTED = "rejected", _("Rejected / Closed")
        WITHDRAWN = "withdrawn", _("Withdrawn by user")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="support_tickets",
        help_text=_("User who raised the ticket"))
    category = models.CharField(max_length=20, choices=Category.choices,
                                default=Category.ACCESS)
    affiliate_link = models.ForeignKey(
        AffiliateLink, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tickets",
        help_text=_("Which referral link was used (access requests only)"))
    plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="support_tickets",
        help_text=_("Assigned by the approver at approval time (access requests)"))
    message = models.TextField(
        help_text=_("Describe the request or issue"))
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING)
    admin_notes = models.TextField(
        blank=True, default="",
        help_text=_("Shown to the user on rejection (and stored for approved tickets)"))
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_support_tickets")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("support ticket")
        verbose_name_plural = _("support tickets")
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="pending"),
                name="uniq_one_pending_ticket_per_user",
            ),
        ]

    def __str__(self):
        return f"Ticket {self.short_id} ({self.user}) - {self.get_category_display()} [{self.status}]"

    @property
    def short_id(self):
        return str(self.id)[:8]

    @property
    def is_access_request(self):
        return self.category == self.Category.ACCESS

    def clean(self):
        super().clean()
        if self.category == self.Category.ACCESS and self.affiliate_link_id is None:
            raise ValidationError({
                "affiliate_link": _("Select the referral link you used.")
            })

    def can_be_reviewed_by(self, user):
        """Superuser, staff listed on the assigned plan, or any staff if the
        plan has no approver list."""
        if not user.is_authenticated or not user.is_staff:
            return False
        if user.is_superuser:
            return True
        if self.plan_id and self.plan.ticket_approvers.exists():
            return self.plan.ticket_approvers.filter(pk=user.pk).exists()
        return True  # no plan yet, or plan with an empty approver list


class TicketAttachment(models.Model):
    """Screenshot attached to a SupportTicket."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=ticket_attachment_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _("ticket attachment")
        verbose_name_plural = _("ticket attachments")

    def __str__(self):
        return f"Attachment for ticket {self.ticket_id}"

    def clean(self):
        super().clean()
        if self.file:
            name = getattr(self.file, "name", "") or ""
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                raise ValidationError({
                    "file": _("Only image files are allowed "
                              "(PNG, JPG, JPEG, GIF, WEBP).")
                })
            size = getattr(self.file, "size", 0) or 0
            if size > MAX_ATTACHMENT_BYTES:
                raise ValidationError({
                    "file": _("File is too large (max 5 MB).")
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
