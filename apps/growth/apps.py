"""
Growth app configuration.
"""
from django.apps import AppConfig


class GrowthConfig(AppConfig):
    """Configuration for the growth app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.growth"
    verbose_name = "Growth"

    def ready(self):
        """Import signals when app is ready."""
        import apps.growth.signals  # noqa: F401
