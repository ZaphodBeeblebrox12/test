
# Create file: apps/growth/management/commands/init_referral_settings.py

from django.core.management.base import BaseCommand
from apps.growth.models import ReferralSettings

class Command(BaseCommand):
    help = 'Initialize referral settings with safe defaults'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reward',
            type=int,
            default=1000,
            help='Reward amount in cents (default: 1000 = $10.00)'
        )
        parser.add_argument(
            '--hold-hours',
            type=int,
            default=72,
            help='Hold duration in hours (default: 72 = 3 days)'
        )

    def handle(self, *args, **options):
        reward_cents = options['reward']
        hold_hours = options['hold_hours']

        # Get or create settings
        settings, created = ReferralSettings.objects.get_or_create(pk=1)

        # Try to set fields (handles any field names)
        fields_updated = []

        # Reward amount
        for field_name in ['reward_amount_cents', 'reward_cents', 'amount_cents']:
            if hasattr(settings, field_name):
                setattr(settings, field_name, reward_cents)
                fields_updated.append(f"{field_name}={reward_cents}")
                break

        # Hold duration
        for field_name in ['hold_duration_hours', 'hold_hours', 'pending_hours']:
            if hasattr(settings, field_name):
                setattr(settings, field_name, hold_hours)
                fields_updated.append(f"{field_name}={hold_hours}")
                break

        settings.save()

        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f'{action} ReferralSettings: {", ".join(fields_updated)}'
            )
        )

        # Show current effective config
        from apps.growth.views import get_referral_config
        config = get_referral_config()
        self.stdout.write(f"Effective config: ${config['reward_amount_cents']/100:.2f} reward, {config['hold_duration_hours']}h hold")
