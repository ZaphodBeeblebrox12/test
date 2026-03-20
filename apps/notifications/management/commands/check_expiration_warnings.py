"""
Management command to check for subscription expiration warnings.

Run this via cron or celery beat every hour:
    python manage.py check_expiration_warnings
"""
from django.core.management.base import BaseCommand
from apps.notifications.signals import check_expiration_warnings


class Command(BaseCommand):
    help = 'Check for subscriptions expiring soon and send warning notifications'

    def handle(self, *args, **options):
        self.stdout.write('Checking for expiration warnings...')

        check_expiration_warnings()

        self.stdout.write(self.style.SUCCESS('Expiration check completed'))
