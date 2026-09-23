"""Batch-refresh channel membership snapshots (scheduled via admin now).

The periodic schedule lives in Django admin (Jobs > Periodic jobs); this
command remains for manual runs:

    python manage.py sync_channel_memberships
    python manage.py sync_channel_memberships --sleep 0.1
"""
from django.core.management.base import BaseCommand

from apps.bot_integration.services.channel_sync import (
    sync_all_channel_memberships,
)


class Command(BaseCommand):
    help = "Refresh channel membership snapshots (free/community channels, display only)."

    def add_arguments(self, parser):
        parser.add_argument("--sleep", type=float, default=0.05,
                            help="Seconds between user syncs. Default 0.05.")

    def handle(self, *args, **options):
        done = sync_all_channel_memberships(sleep=options["sleep"])
        self.stdout.write(self.style.SUCCESS(
            f"sync_channel_memberships: refreshed snapshots for {done} linked account(s)."))
