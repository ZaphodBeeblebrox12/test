"""
Management command to seed sample pricing data.
Run: python manage.py seed_pricing
"""
from django.core.management.base import BaseCommand
from apps.subscriptions.models import Plan, PlanPrice


class Command(BaseCommand):
    help = 'Seed sample pricing plans for landing page'

    def handle(self, *args, **kwargs):
        self.stdout.write('Creating sample plans...')

        plans = [
            {
                'tier': 'basic',
                'name': 'Starter',
                'description': 'Perfect for traders learning real-time execution',
                'order': 1,
                'price': 4700,
            },
            {
                'tier': 'pro',
                'name': 'Pro',
                'description': 'For serious traders who want unlimited guidance',
                'order': 2,
                'price': 8100,
            },
            {
                'tier': 'enterprise',
                'name': 'Elite',
                'description': 'White-glove service for professional traders',
                'order': 3,
                'price': 16400,
            },
        ]

        for data in plans:
            plan, created = Plan.objects.get_or_create(
                tier=data['tier'],
                defaults={
                    'name': data['name'],
                    'description': data['description'],
                    'display_order': data['order'],
                    'is_active': True,
                    'max_projects': 100,
                    'max_storage_mb': 1000,
                    'api_calls_per_day': 1000,
                }
            )

            if created:
                PlanPrice.objects.create(
                    plan=plan,
                    interval='monthly',
                    price_cents=data['price'],
                    currency='USD'
                )
                self.stdout.write(self.style.SUCCESS(f'Created {plan.name}'))
            else:
                self.stdout.write(f'{plan.name} already exists')

        self.stdout.write(self.style.SUCCESS('Done!'))
