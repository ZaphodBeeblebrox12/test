import uuid
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.growth.models import ReferralCode, ReferralSettings, ReferralReward
from apps.growth.services import ReferralService

User = get_user_model()


class Command(BaseCommand):
    help = "Test the complete referral lifecycle"

    def handle(self, *args, **options):
        suffix = uuid.uuid4().hex[:8]
        self.stdout.write("=== Referral flow shell test ===")
        self.stdout.write(f"suffix: {suffix}")

        with transaction.atomic():
            # Create users
            referrer = User.objects.create_user(
                username=f"shell_referrer_{suffix}",
                email=f"referrer_{suffix}@example.com",
                password="test-pass-123",
            )
            referred = User.objects.create_user(
                username=f"shell_referred_{suffix}",
                email=f"referred_{suffix}@example.com",
                password="test-pass-123",
            )
            self.stdout.write(f"Created referrer: {referrer.pk} {referrer.username}")
            self.stdout.write(f"Created referred: {referred.pk} {referred.username}")

            # Settings
            rs = ReferralSettings.get_settings()
            if not rs:
                self.stdout.write(self.style.ERROR("FAIL: No ReferralSettings found."))
                return
            if not rs.rewards_enabled:
                self.stdout.write(self.style.ERROR("FAIL: Referral rewards are disabled."))
                return

            self.stdout.write(f"Rewards enabled: {rs.rewards_enabled}, min purchase: {rs.minimum_purchase_amount_cents}, reward %: {rs.default_reward_percentage}")

            # Referral code
            ref_code = ReferralCode.get_or_create_for_user(referrer)
            self.stdout.write(f"Referral code: {ref_code.code}")

            # Record signup
            referral = ReferralService.record_referral_signup(referred, ref_code.code)
            if not referral:
                self.stdout.write(self.style.ERROR("FAIL: No referral created."))
                return
            self.stdout.write(f"OK: Referral id={referral.pk}")

            # Simulate purchase
            purchase_cents = 10_000
            if purchase_cents < rs.minimum_purchase_amount_cents:
                self.stdout.write(self.style.ERROR(f"Purchase below minimum {rs.minimum_purchase_amount_cents}"))
                return

            expected_cents = int(purchase_cents * (rs.default_reward_percentage / Decimal("100")))
            self.stdout.write(f"Expected reward: {expected_cents} cents")

            completed = ReferralService.complete_referral_on_purchase(
                referred,
                purchase_amount_cents=purchase_cents,
                currency="USD",
                triggering_subscription=None,
            )
            self.stdout.write(f"complete_referral_on_purchase returned: {completed}")

            referral.refresh_from_db()
            self.stdout.write(f"Referral status: {referral.status}")

            try:
                reward = referral.reward
                self.stdout.write(f"Reward id={reward.pk}, amount={reward.amount_cents}, status={reward.status}")
                if reward.amount_cents != expected_cents:
                    self.stdout.write(self.style.ERROR(f"Amount mismatch: {reward.amount_cents} vs {expected_cents}"))
                    return
                if reward.status not in ["pending", "credited"]:
                    self.stdout.write(self.style.ERROR(f"Bad status: {reward.status}"))
                    return
                self.stdout.write(self.style.SUCCESS("All checks passed."))
            except ReferralReward.DoesNotExist:
                self.stdout.write(self.style.ERROR("No reward created."))
                return

        self.stdout.write(self.style.SUCCESS("=== Test passed ==="))