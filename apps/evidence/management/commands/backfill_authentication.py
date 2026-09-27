# Backfill PaymentAuthentication for payments that lack it.
from django.core.management.base import BaseCommand

from apps.evidence.services import backfill_payment_authentication
from apps.payments.models import PaymentIntent


class Command(BaseCommand):
    help = 'Backfill PaymentAuthentication (idempotent, safe to re-run).'

    def add_arguments(self, parser):
        parser.add_argument('--provider', choices=['stripe', 'razorpay'])
        parser.add_argument('--limit', type=int, default=500)
        parser.add_argument('--only-success', action='store_true')

    def handle(self, *args, **options):
        qs = PaymentIntent.objects.filter(authentication__isnull=True)
        if options['provider']:
            qs = qs.filter(provider=options['provider'])
        if options['only_success']:
            qs = qs.filter(status=PaymentIntent.Status.SUCCESS)
        done = failed = 0
        for intent in qs.order_by('-created_at')[:options['limit']]:
            try:
                if backfill_payment_authentication(intent) is not None:
                    done += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f'intent {intent.pk}: {exc}')
        self.stdout.write(self.style.SUCCESS(
            f'backfilled={done} unavailable={failed}'))
