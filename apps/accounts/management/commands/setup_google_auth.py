"""
Management command to setup Google OAuth app for allauth.
Run this after migrations to auto-create the SocialApp.
"""
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Setup Google OAuth SocialApp for allauth'

    def handle(self, *args, **options):
        # Get or create site
        site, created = Site.objects.get_or_create(
            pk=1,
            defaults={
                'domain': '127.0.0.1:8000',
                'name': 'Community Platform'
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created site: {site.name}'))
        else:
            self.stdout.write(f'Site exists: {site.name}')

        # Get or create Google SocialApp
        app, created = SocialApp.objects.get_or_create(
            provider='google',
            defaults={
                'name': 'Google',
                'client_id': 'placeholder-client-id',
                'secret': 'placeholder-secret',
            }
        )

        # Add site to app
        if site not in app.sites.all():
            app.sites.add(site)
            self.stdout.write(self.style.SUCCESS(f'Added site to Google app'))

        if created:
            self.stdout.write(self.style.SUCCESS('Created Google SocialApp'))
            self.stdout.write(self.style.WARNING(
                '\nIMPORTANT: You must update the client_id and secret in Django Admin!\n'
                'Go to: http://127.0.0.1:8000/admin/socialaccount/socialapp/\n'
                'Or run: python manage.py shell\n'
                '>>> from allauth.socialaccount.models import SocialApp\n'
                '>>> app = SocialApp.objects.get(provider="google")\n'
                '>>> app.client_id = "your-real-client-id"\n'
                '>>> app.secret = "your-real-secret"\n'
                '>>> app.save()'
            ))
        else:
            self.stdout.write('Google SocialApp already exists')

        self.stdout.write(self.style.SUCCESS('\nSetup complete!'))
