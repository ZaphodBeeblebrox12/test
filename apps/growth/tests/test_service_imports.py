"""Smoke test: growth services package exposes the public API after the split."""
from django.test import SimpleTestCase


class GrowthServiceImportsTest(SimpleTestCase):
    def test_public_names_importable(self):
        from apps.growth.services.rewards import (
            UserRewardBalance, ReferralRewardService, SubscriptionCreditService)
        from apps.growth.services.referrals import ReferralService
        from apps.growth.services.gifts import (
            GiftService, GiftClaimService, GiftEmailService, LegacyGiftService,
            GiftServiceError, GiftAlreadyClaimedError, GiftExpiredError,
            GiftEmailMismatchError, SelfGiftError, InvalidGiftCodeError,
            AttributionRequiredError)
        self.assertTrue(all([
            UserRewardBalance, ReferralRewardService, SubscriptionCreditService,
            ReferralService, GiftService, GiftClaimService, GiftEmailService,
            LegacyGiftService]))
