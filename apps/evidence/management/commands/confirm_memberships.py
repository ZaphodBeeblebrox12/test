# Reconcile MembershipConfirmation rows from observed snapshots.
from django.core.management.base import BaseCommand

from apps.bot_integration.models import ChannelMembershipSnapshot
from apps.evidence.services import confirm_membership_from_snapshot


class Command(BaseCommand):
    help = ('Create/update MembershipConfirmation rows from the newest '
            'observed membership snapshot per user/platform/external_id.')

    def handle(self, *args, **options):
        newest = {}
        for snap in ChannelMembershipSnapshot.objects.filter(
                is_member=True).order_by('checked_at'):
            newest[(snap.user_id, snap.platform, snap.external_id)] = snap
        ensured = 0
        for snap in newest.values():
            if confirm_membership_from_snapshot(snap) is not None:
                ensured += 1
        self.stdout.write(self.style.SUCCESS(
            f'confirmations ensured for {ensured} memberships'))
