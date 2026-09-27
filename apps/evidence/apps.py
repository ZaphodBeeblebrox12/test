"""Chargeback evidence substrate.

Holds the point-in-time records the dispute system needs, FK-linked to the
existing payments/subscriptions/bot ledgers. Deliberately separate from
apps.payments so the financial core stays untouched (drop-in safety); every
row references existing source-of-truth records rather than duplicating them.
"""
from django.apps import AppConfig


class EvidenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.evidence"
    verbose_name = "Evidence"
