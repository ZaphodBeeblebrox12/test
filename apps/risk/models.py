import uuid

from django.conf import settings
from django.db import models


class RiskSignal(models.Model):
    """Append-only INTERNAL decision support.

    NEVER network-facing evidence. Only salted hashes of shared identifiers
    live here; raw identifiers stay in their own restricted tables.
    Review fields (reviewed_*, disposition) are the only mutable columns.
    """
    class SignalType(models.TextChoices):
        VELOCITY = "velocity", "Signup-to-payment velocity"
        MULTI_ACCOUNT_CARD = "multi_account_card", "Shared payment credential"
        MULTI_ACCOUNT_DEVICE = "multi_account_device", "Shared device"
        TRIAL_ABUSE = "trial_abuse", "Trial abuse pattern"
        PRIOR_DISPUTE = "prior_dispute", "Prior dispute / chargeback"
        REFUND_PATTERN = "refund_pattern", "Repeat refund pattern"

    MUTABLE = ("reviewed_by_id", "reviewed_at", "disposition")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE,
                             related_name="risk_signals")
    signal_type = models.CharField(max_length=30, choices=SignalType.choices)
    severity = models.PositiveSmallIntegerField(default=3)  # 1-5
    subject_hash = models.CharField(max_length=64, blank=True, default="",
                                    db_index=True)
    detail = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=20, default="rule")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    disposition = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["signal_type", "created_at"])]

    def __str__(self):
        return f"{self.get_signal_type_display()} (sev {self.severity})"
