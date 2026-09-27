"""Chargeback case management.

First-class dispute cases linking the existing immutable evidence substrate.
Internal risk signals never live here and never leak into packages.
"""
from django.apps import AppConfig


class DisputesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.disputes"
    verbose_name = "Disputes"
