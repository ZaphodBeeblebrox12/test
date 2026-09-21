"""G2: Campaigns + Audiences on the G1 email foundation.

Design:
- Campaign references an immutable TemplateVersion SNAPSHOT (editing the
  template never changes an in-flight/sent campaign).
- Audience: a reusable, composable recipient query (stored as structured
  rules).  Membership is materialized into CampaignRecipient rows at schedule
  time = the audience snapshot (proves exactly who was eligible).
- CampaignRecipient: one row per (campaign, user).  Its unique constraint is
  the send-idempotency guarantee; a durable job claims each QUEUED recipient
  atomically and creates a G1 Delivery (idempotency_key = campaign:user).
- CampaignMetrics: per-campaign cached counters updated idempotently from the
  Delivery send-sweep, so analytics reads are O(1) and never rescan deliveries.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F

from apps.emailing.models import TemplateVersion


class Audience(models.Model):
    """Reusable recipient segment, stored as structured rules (a JSON filter
    over Users).  Rules are evaluated at snapshot time, not per send, so a
    campaign's membership is frozen and provable."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    # Structured filter over the User model, e.g.
    # {"is_active": True, "date_joined__gte": "2026-01-01", "email__icontains": "@"}
    rules = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def members(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.filter(**self.rules)


class Campaign(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        SENDING = "sending", "Sending"
        PAUSED = "paused", "Paused"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    # Immutable template snapshot: a frozen TemplateVersion.  Editing the
    # template creates a new version; this campaign keeps its snapshot.
    template_version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT,
                                         related_name="campaigns")
    audience = models.ForeignKey(Audience, on_delete=models.PROTECT, related_name="campaigns")
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    subject_override = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when membership is frozen = the audience snapshot.
    snapshot_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class CampaignRecipient(models.Model):
    """One row per (campaign, user) = the audience snapshot + send idempotency.

    Unique (campaign, user) guarantees a recipient is sent to at most once.
    A durable job atomically claims QUEUED rows (conditional UPDATE) and
    creates a G1 Delivery."""
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Suppressed"
        SKIPPED = "skipped", "Skipped"   # opted-out / suppressed at snapshot

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="campaign_recipients")
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    delivery = models.ForeignKey("emailing.Delivery", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="campaign_recipients")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("campaign", "user")]
        indexes = [models.Index(fields=["campaign", "status"])]

    def __str__(self):
        return f"{self.campaign}:{self.email}"


class CampaignMetrics(models.Model):
    """Per-campaign cached counters, updated idempotently by the send-sweep.

    Denormalized so analytics reads are O(1); the send-sweep is the only
    writer and it updates counters from claimed recipient transitions."""
    campaign = models.OneToOneField(Campaign, on_delete=models.CASCADE,
                                    related_name="metrics", primary_key=True)
    total = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    bounced = models.PositiveIntegerField(default=0)
    complained = models.PositiveIntegerField(default=0)
    suppressed = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"metrics:{self.campaign_id}"
