from django.apps import AppConfig


class GrowthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.growth"
    verbose_name = "Growth"

    def ready(self):
        import apps.growth.signals
