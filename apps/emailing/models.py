"""G1 email foundation: templates (immutable versions), media, delivery.

Design:
- Template owns identity; TemplateVersion is immutable once created/used.
- HTML is the canonical render source.  editor_mode "visual" generates HTML;
  editor_mode "html" keeps user HTML verbatim (never rewritten/normalized).
- Delivery is a provider-agnostic abstraction with an idempotency_key so a
  logical message is sent at most once.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class VariableSpec(models.Model):
    """Declared, validated template variable (never arbitrary execution)."""
    name = models.CharField(max_length=50, unique=True)  # e.g. first_name
    description = models.CharField(max_length=200, blank=True, default="")
    default_value = models.CharField(max_length=255, blank=True, default="")
    preview_value = models.CharField(max_length=255, blank=True, default="")
    required = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class MediaAsset(models.Model):
    """Reusable email media (referenced by URL, never base64-embedded)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to="email_media/%Y/%m/")
    public_url = models.URLField(help_text="HTTPS public URL to serve the asset.")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.alt_text or self.public_url


class Template(models.Model):
    class Kind(models.TextChoices):
        TRANSACTIONAL = "transactional", "Transactional"
        MARKETING = "marketing", "Marketing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MARKETING)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TemplateVersion(models.Model):
    """Immutable, renderable snapshot of a Template.  HTML is authoritative."""
    class EditorMode(models.TextChoices):
        VISUAL = "visual", "Visual builder"
        HTML = "html", "Raw HTML"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    editor_mode = models.CharField(max_length=10, choices=EditorMode.choices)
    subject = models.CharField(max_length=255)
    preview_text = models.CharField(max_length=255, blank=True, default="")
    html = models.TextField(help_text="Canonical render source. Preserved verbatim for editor_mode=html.")
    plain_text = models.TextField(blank=True, default="")
    visual_blocks = models.JSONField(default=list, blank=True)  # only when editor_mode=visual
    variables = models.ManyToManyField(VariableSpec, blank=True, related_name="template_versions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("template", "version_number")]
        ordering = ["template", "-version_number"]

    def clean(self):
        if self.editor_mode == self.EditorMode.VISUAL and not self.visual_blocks:
            raise ValidationError({"visual_blocks": "Visual mode requires at least one block."})
        if self.editor_mode == self.EditorMode.HTML and not self.html.strip():
            raise ValidationError({"html": "HTML mode requires HTML content."})

    def __str__(self):
        return f"{self.template.name} v{self.version_number}"


class ContentBlock(models.Model):
    """Reusable visual content block for the builder."""
    name = models.CharField(max_length=120)
    block_type = models.CharField(max_length=30)  # heading,text,image,button,divider,spacer,columns,footer,social
    schema = models.JSONField(default=dict)  # stable block schema {type, props}
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Delivery(models.Model):
    """Provider-agnostic delivery record with idempotency + bounce/complaint."""
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        BOUNCED = "bounced", "Bounced"
        COMPLAINED = "complained", "Complained"
        SUPPRESSED = "suppressed", "Suppressed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    template_version = models.ForeignKey(TemplateVersion, null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="deliveries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="email_deliveries")
    to_email = models.EmailField()
    kind = models.CharField(max_length=20, choices=Template.Kind.choices,
                            default=Template.Kind.MARKETING)
    subject = models.CharField(max_length=255, blank=True, default="")
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED)
    provider = models.CharField(max_length=30, blank=True, default="")
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.to_email}:{self.idempotency_key}"
