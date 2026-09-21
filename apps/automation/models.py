"""G3: event-driven lifecycle automation.

An AutomationRule watches a durable Event type.  When a matching Event is
created, a rule enqueues an AutomationRun (delayed via the durable-job
next_at).  At fire time the run RE-CHECKS eligibility (consent/suppression)
then executes the action (send a frozen TemplateVersion) through the G2
idempotent Delivery.  Idempotency: unique (rule, triggering_event) means an
event can trigger a rule at most once.
"""
import uuid

from django.conf import settings
from django.db import models

from apps.emailing.models import TemplateVersion
from apps.events.models import Event


class AutomationRule(models.Model):
    """'When <event> occurs, send <template> after <delay>, if eligible.'"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    trigger_event_type = models.CharField(max_length=50, choices=Event.Type.choices)
    template_version = models.ForeignKey(TemplateVersion, on_delete=models.PROTECT,
                                         related_name="automation_rules")
    delay_minutes = models.PositiveIntegerField(default=0,
                                                help_text="Wait this long after the event before firing.")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class AutomationRun(models.Model):
    """One (rule, event) execution.  Unique together = event triggers a rule
    at most once, regardless of duplicate event delivery or re-processing."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        FIRED = "fired", "Fired"
        SKIPPED = "skipped", "Skipped"     # ineligible at fire time
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name="runs")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="automation_runs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="automation_runs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    delivery = models.ForeignKey("emailing.Delivery", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="automation_runs")
    error = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField()
    fired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("rule", "event")]
        indexes = [models.Index(fields=["status", "scheduled_at"])]

    def __str__(self):
        return f"{self.rule}:{self.event.dedupe_key}"
