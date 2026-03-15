"""
Management command to create SocialApp from environment variables.
Usage: python manage.py create_socialapp
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = "Create Google SocialApp from environment variables"

    def handle(self, *args, **options):
        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        secret = os.getenv("GOOGLE_OAUTH_SECRET")

        if not client_id or not secret:
            self.stdout.write(
                self.style.WARNING(
                    "GOOGLE_OAUTH_CLIENT_ID or GOOGLE_OAUTH_SECRET not set. "
                    "Skipping SocialApp creation."
                )
            )
            return

        # Get or create site
        site, _ = Site.objects.get_or_create(
            id=1,
            defaults={"domain": "localhost:8000", "name": "localhost"}
        )

        # Get or create Google SocialApp
        app, created = SocialApp.objects.get_or_create(
            provider="google",
            defaults={
                "name": "Google",
                "client_id": client_id,
                "secret": secret,
            }
        )

        if not created:
            # Update existing
            app.client_id = client_id
            app.secret = secret
            app.save()
            self.stdout.write(self.style.SUCCESS("Updated Google SocialApp"))
        else:
            app.sites.add(site)
            self.stdout.write(self.style.SUCCESS("Created Google SocialApp"))

        # Ensure site is linked
        if site not in app.sites.all():
            app.sites.add(site)
            self.stdout.write(self.style.SUCCESS("Linked SocialApp to site"))
