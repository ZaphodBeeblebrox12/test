from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jobs"

    def ready(self):
        # Start the admin-managed periodic scheduler only in long-lived server
        # processes; skip one-shot management commands (migrate/test/shell...).
        import sys

        skip_commands = {
            "migrate", "makemigrations", "test", "shell", "dbshell",
            "collectstatic", "check", "loaddata", "dumpdata", "diffsettings",
            "sendtestemail", "createsuperuser", "makemessages", "compilemessages",
            "sync_channel_memberships",
        }
        if set(sys.argv) & skip_commands:
            return
        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "periodic scheduler failed to start")
