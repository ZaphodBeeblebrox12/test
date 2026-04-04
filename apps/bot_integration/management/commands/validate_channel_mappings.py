from django.core.management.base import BaseCommand
from apps.bot_integration.models import PlanChannelMapping
from apps.bot_integration.services.telegram import TelegramBotService


class Command(BaseCommand):
    help = "Validate all Telegram channel mappings (bot admin rights, invite link creation)"

    def handle(self, *args, **options):
        mappings = PlanChannelMapping.objects.filter(platform='telegram')
        if not mappings:
            self.stdout.write("No Telegram channel mappings found.")
            return

        for mapping in mappings:
            self.stdout.write(f"Checking: {mapping.plan.name} -> {mapping.external_id}")
            invite = TelegramBotService.create_one_time_invite_link(mapping.external_id)
            if invite:
                self.stdout.write(self.style.SUCCESS(f"  ✅ OK – invite link created"))
            else:
                self.stdout.write(self.style.ERROR(f"  ❌ FAILED – bot may not be admin or channel ID invalid"))