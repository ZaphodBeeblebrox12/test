"""Django-native durable job system (transactional outbox + worker).

Replaces Celery/Redis.  The Job row is written in the SAME transaction as
the business-state change, giving atomic "state + job" commits on both
SQLite (dev) and PostgreSQL (prod).
"""

import uuid
from django.db import models
from django.utils import timezone


class ReconcileState(models.Model):
    """One row per user; monotonically increasing reconciliation version."""
    user = models.OneToOneField("accounts.User", on_delete=models.CASCADE,
                                related_name="reconcile_state")
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"reconcile-state u{self.user_id} v{self.version}"


class Job(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]
    OPEN_STATUSES = (STATUS_PENDING, STATUS_RUNNING)

    kind = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default=STATUS_PENDING)
    requested_version = models.BigIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    # Scoped idempotency: unique only among OPEN jobs (partial constraint).
    idempotency_key = models.CharField(max_length=160, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"]),
            models.Index(fields=["kind"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(status__in=("pending", "running")),
                name="jobs_open_key_uniq",
            ),
        ]

    def __str__(self):
        return f"Job<{self.kind}#{self.pk} {self.status}>"


class ProvisioningOperation(models.Model):
    """Persisted external side-effect state machine (grant/revoke).

    One row per provisioning intent/lifecycle.  Retries reuse the same
    operation id and the same persisted invite link; a NEW grant after a
    completed revoke gets a NEW row (new id, new key).
    """

    OP_GRANT = "grant"
    OP_REVOKE = "revoke"
    OP_CHOICES = [(OP_GRANT, "Grant"), (OP_REVOKE, "Revoke")]

    ST_PENDING = "pending"
    ST_CREATING = "creating"
    ST_CREATED = "created"
    ST_SENDING = "sending"
    ST_SENT = "sent"
    ST_UNKNOWN = "unknown_external_state"
    ST_COMPLETED = "completed"
    ST_FAILED = "failed"
    ST_CHOICES = [(s, s.replace("_", " ").title()) for s in (
        ST_PENDING, ST_CREATING, ST_CREATED, ST_SENDING, ST_SENT,
        ST_UNKNOWN, ST_COMPLETED, ST_FAILED)]

    operation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    operation = models.CharField(max_length=16, choices=OP_CHOICES)
    user_id = models.UUIDField()   # matches accounts.User PK (UUID)
    channel_id = models.CharField(max_length=64)
    state = models.CharField(max_length=24, choices=ST_CHOICES, default=ST_PENDING)
    invite_link = models.CharField(max_length=512, null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)  # SQLite int64-safe in practice
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state"]),
                   models.Index(fields=["user_id", "channel_id"])]

    @property
    def provision_key(self):
        return f"{self.operation}:{self.operation_id}"

    def __str__(self):
        return f"{self.operation}:{self.operation_id} [{self.state}]"
