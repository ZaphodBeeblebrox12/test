"""Durable domain Event foundation (G0).

A queryable, idempotent record that a business transition happened.  The
authoritative service owns the transition; this only records it (plus enough
references to feed Growth/Email/analytics later).  Events are NOT a second
source of truth -- carry references and a compact payload, not full state.

Idempotency: `dedupe_key` is deterministic per (event_type, domain object);
create() uses get-or-create on it so retries/duplicate emissions store once.
"""
import uuid

from django.conf import settings
from django.db import models


class Event(models.Model):
    class Type(models.TextChoices):
        # payments
        PURCHASE_COMPLETED = "purchase.completed", "Purchase completed"
        PAYMENT_FAILED = "payment.failed", "Payment failed"
        RENEWAL_COMPLETED = "renewal.completed", "Renewal completed"
        # subscriptions
        SUBSCRIPTION_ACTIVATED = "subscription.activated", "Subscription activated"
        SUBSCRIPTION_CANCELED = "subscription.canceled", "Subscription canceled"
        SUBSCRIPTION_EXPIRED = "subscription.expired", "Subscription expired"
        TRIAL_STARTED = "trial.started", "Trial started"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=50, choices=Type.choices)
    dedupe_key = models.CharField(max_length=255, unique=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="events")
    # Generic references to the originating object (e.g. payment:<uuid>,
    # subscription:<uuid>).  Keeps events decoupled from concrete FKs.
    object_ref = models.CharField(max_length=100, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["event_type", "occurred_at"])]

    def __str__(self):
        return f"{self.event_type}:{self.dedupe_key}"


def record_event(event_type, *, dedupe_key, user_id=None, object_ref="", payload=None,
                 occurred_at=None):
    """Idempotent event creation.  Returns (event, created)."""
    from django.utils import timezone
    obj, created = Event.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "event_type": event_type,
            "user_id": user_id,
            "object_ref": object_ref,
            "payload": payload or {},
            "occurred_at": occurred_at or timezone.now(),
        },
    )
    return obj, created
