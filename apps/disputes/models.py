"""Dispute case records: header (case state), append-only lifecycle
(hash-chained), and reproducible evidence packages."""
import hashlib
import json
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


class Dispute(models.Model):
    """One chargeback case. Source of truth for case state; the financial
    facts stay in the immutable PaymentIntent."""

    class ReasonCategory(models.TextChoices):
        FRAUD = "fraud", "Fraud - card absent"
        UNRECOGNIZED = "unrecognized", "Unrecognized transaction"
        NOT_RECEIVED = "not_received", "Service not received"
        NOT_AS_DESCRIBED = "not_as_described", "Not as described"
        RECURRING_CANCELLED = "recurring_cancelled", "Recurring/cancelled"
        CREDIT_NOT_PROCESSED = "credit_not_processed", "Credit not processed"
        DUPLICATE = "duplicate", "Duplicate transaction"
        CUSTOMER_INITIATED = "customer_initiated", "Customer initiated"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPENED = "opened", "Opened"
        EVIDENCE_PREP = "evidence_prep", "Evidence preparation"
        READY_FOR_REVIEW = "ready_for_review", "Ready for review"
        SUBMITTED = "submitted", "Evidence submitted"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        WITHDRAWN = "withdrawn", "Withdrawn by customer/issuer"
        ACCEPTED = "accepted", "Accepted (merchant conceded)"

    TERMINAL = (Status.WON, Status.LOST, Status.WITHDRAWN, Status.ACCEPTED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_intent = models.ForeignKey(
        "payments.PaymentIntent", on_delete=models.CASCADE,
        related_name="disputes")
    provider = models.CharField(max_length=20, choices=(
        ("stripe", "Stripe"), ("razorpay", "Razorpay")))
    provider_dispute_id = models.CharField(max_length=255)
    network = models.CharField(max_length=20, blank=True, default="")
    reason_raw = models.CharField(max_length=50, blank=True, default="",
                                  help_text="Provider reason, verbatim.")
    reason_category = models.CharField(
        max_length=30, choices=ReasonCategory.choices,
        default=ReasonCategory.OTHER)
    amount_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=25, choices=Status.choices,
                              default=Status.OPENED)
    opened_at = models.DateTimeField(default=timezone.now)
    evidence_due_at = models.DateTimeField(null=True, blank=True,
                                           help_text="Provider response deadline.")
    funds_withdrawn_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    claim_text = models.TextField(blank=True, default="")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_disputes")
    access_actioned = models.BooleanField(
        default=False,
        help_text="Confirmed-loss access revocation ran exactly once.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_dispute_id"],
                                    name="disputes_provider_case_unique"),
        ]
        indexes = [models.Index(fields=["status"]),
                   models.Index(fields=["evidence_due_at"]),
                   models.Index(fields=["payment_intent"])]

    def __str__(self):
        return f"Dispute {self.provider_dispute_id} [{self.status}]"


class DisputeEvent(models.Model):
    """Append-only, hash-chained case lifecycle trail."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        STATUS_CHANGED = "status_changed", "Status changed"
        NOTE = "note", "Note"
        EVIDENCE_GENERATED = "evidence_generated", "Evidence generated"
        EVIDENCE_SUBMITTED = "evidence_submitted", "Evidence submitted"
        ACCESS_ACTION = "access_action", "Access action"
        CUSTOMER_CONTACTED = "customer_contacted", "Customer contacted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dispute = models.ForeignKey(Dispute, on_delete=models.CASCADE,
                                related_name="events")
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              help_text="Null = system/webhook.")
    from_status = models.CharField(max_length=25, blank=True, default="")
    to_status = models.CharField(max_length=25, blank=True, default="")
    note = models.TextField(blank=True, default="")
    occurred_at = models.DateTimeField(default=timezone.now)
    prev_hash = models.CharField(max_length=64, blank=True, default="")
    content_hash = models.CharField(max_length=64)

    class Meta:
        ordering = ["occurred_at", "created_at"]
        indexes = [models.Index(fields=["dispute", "occurred_at"])]
        get_latest_by = "occurred_at"

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # NOTE: UUID pk fields are assigned at instantiation, so self.pk is
        # always set - even for new rows. Only block real updates:
        # creates arrive with force_insert=True from objects.create().
        if self.pk and not kwargs.get("force_insert"):
            raise RuntimeError("DisputeEvent is append-only.")
        if not self.prev_hash:
            last = (DisputeEvent.objects.filter(dispute=self.dispute)
                    .order_by("-occurred_at", "-created_at").first())
            self.prev_hash = last.content_hash if last else ""
        payload = _canonical({
            "dispute": str(self.dispute_id), "type": self.event_type,
            "actor": str(self.actor_id or ""), "from": self.from_status,
            "to": self.to_status, "note": self.note,
            "at": str(self.occurred_at), "prev": self.prev_hash})
        self.content_hash = _sha(payload)
        super().save(*args, **kwargs)


class DisputeEvidencePackage(models.Model):
    """Reproducible, immutable-once-submitted evidence package.

    Regenerating produces a NEW package row; submitted packages never mutate.
    """

    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        WITHDRAWN = "withdrawn", "Withdrawn"
        ACCEPTED = "accepted", "Accepted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dispute = models.ForeignKey(Dispute, on_delete=models.CASCADE,
                                related_name="packages")
    generator_version = models.CharField(max_length=20, default="1")
    reason_category = models.CharField(max_length=30, default="other")
    sections = models.JSONField(default=dict)
    timeline = models.JSONField(default=list)
    narrative_draft = models.TextField(blank=True, default="")
    narrative_final = models.TextField(blank=True, default="")
    readiness = models.JSONField(default=dict)
    content_sha256 = models.CharField(max_length=64)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="generated_packages")
    generated_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="submitted_packages")
    submitted_via = models.CharField(max_length=20, blank=True, default="")
    submission_reference = models.CharField(max_length=255, blank=True, default="")
    outcome = models.CharField(max_length=20, choices=Outcome.choices,
                               default=Outcome.PENDING)

    class Meta:
        ordering = ["-generated_at"]
        indexes = [models.Index(fields=["dispute", "generated_at"])]

    def save(self, *args, **kwargs):
        if self.pk and self.submitted_at and "submitted_at" not in (
                kwargs.get("update_fields") or []):
            raise RuntimeError(
                "A submitted DisputeEvidencePackage is immutable; generate a "
                "new package instead.")
        if not self.content_sha256:
            self.content_sha256 = _sha(_canonical({
                "sections": self.sections, "timeline": self.timeline,
                "narrative": self.narrative_final or self.narrative_draft,
                "reason": self.reason_category,
                "generator": self.generator_version}))
        super().save(*args, **kwargs)
