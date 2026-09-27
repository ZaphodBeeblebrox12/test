# Verify the append-only hash chain of every dispute event log.
from django.core.management.base import BaseCommand

from apps.disputes.models import Dispute


class Command(BaseCommand):
    help = 'Verify DisputeEvent hash chains; exits non-zero on tampering.'

    def handle(self, *args, **options):
        bad = 0
        for dispute in Dispute.objects.prefetch_related('events'):
            prev = ''
            for ev in dispute.events.all():
                if ev.prev_hash != prev:
                    bad += 1
                    self.stderr.write(
                        f'CHAIN BREAK dispute={dispute.pk} event={ev.pk}')
                    break
                prev = ev.content_hash
        if bad:
            self.stderr.write(self.style.ERROR(f'{bad} chain(s) broken'))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('all dispute chains verified'))
