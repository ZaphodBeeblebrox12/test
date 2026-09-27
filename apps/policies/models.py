"""Immutable, versioned policy artifacts and acceptance records.

First-class treatment: policies are NOT static pages. Every published version
is immutable (content + snapshot + hash never change); changes create NEW
versions. A version moves DRAFT -> ACTIVE (served to new acceptances) ->
ARCHIVED (historical, still viewable - critically from the dispute admin).

Layer 1 (immutable snapshot) of the chargeback integrity architecture.
"""
import hashlib
import uuid

from django.conf import settings
from django.db import models


def _content_sha256(content_html: str) -> str:
    return hashlib.sha256(content_html.encode("utf-8")).hexdigest()


class PolicyVersion(models.Model):
    """One immutable version of one policy document. Never overwrite published
    content: a change creates a new version."""

    class PolicyType(models.TextChoices):
        TERMS = "terms", "Terms of Service"
        REFUND = "refund", "Refund & Cancellation Policy"
        CANCELLATION = "cancellation", "Cancellation Policy"
        RISK = "risk", "Risk Disclosure (Trading Disclaimer)"
        PRIVACY = "privacy", "Privacy Policy"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (not served)"
        ACTIVE = "active", "Active (served to new acceptances)"
        ARCHIVED = "archived", "Archived (historical, still viewable)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_type = models.CharField(max_length=20, choices=PolicyType.choices)
    version = models.CharField(max_length=20, help_text="e.g. '1.0'")
    title = models.CharField(max_length=200)
    content_html = models.TextField(
        help_text="Full text served to users at this version. "
                  "Immutable once published.")
    content_sha256 = models.CharField(max_length=64, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.DRAFT)
    effective_from = models.DateTimeField()
    published_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When this version was first published (made active).")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_policy_versions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["policy_type", "version"],
                                    name="policies_version_unique"),
            models.UniqueConstraint(
                fields=["policy_type"], condition=models.Q(status="active"),
                name="policies_one_active_per_type"),
        ]
        indexes = [models.Index(fields=["policy_type", "status"])]

    def save(self, *args, **kwargs):
        self.content_sha256 = _content_sha256(self.content_html)
        super().save(*args, **kwargs)

    @classmethod
    def active(cls, policy_type: str) -> "PolicyVersion | None":
        return cls.objects.filter(policy_type=policy_type,
                                  status=cls.Status.ACTIVE).first()

    def __str__(self):
        return f"{self.get_policy_type_display()} v{self.version} [{self.status}]"


class PolicyAcceptance(models.Model):
    """Write-once record that a user accepted an exact policy version.

    Tied to: user, the acceptance moment, the exact version, server timestamp,
    IP, user agent, and the checkout/session reference (directly via
    session_ref and, for checkout-scoped acceptances, via CheckoutEvidence).
    """

    class Context(models.TextChoices):
        CHECKOUT = "checkout", "Checkout"
        CANCELLATION = "cancellation", "Cancellation request"
        PROFILE = "profile", "Profile"
        TRIAL = "trial", "Trial claim"
        SIGNUP = "signup", "Signup"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="policy_acceptances")
    policy_version = models.ForeignKey(
        PolicyVersion, on_delete=models.PROTECT, related_name="acceptances")
    context = models.CharField(max_length=20, choices=Context.choices,
                               default=Context.CHECKOUT)
    accepted_at = models.DateTimeField(help_text="Server timestamp of acceptance.")
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True, default="")
    session_ref = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="Salted hash of the Django session key at acceptance time.")
    checkout_evidence = models.ForeignKey(
        "evidence.CheckoutEvidence", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="policy_acceptances")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "accepted_at"]),
                   models.Index(fields=["session_ref"]),
                   models.Index(fields=["policy_version"])]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.get("force_insert"):
            raise RuntimeError(
                "PolicyAcceptance is immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} accepted {self.policy_version} @ {self.accepted_at}"
