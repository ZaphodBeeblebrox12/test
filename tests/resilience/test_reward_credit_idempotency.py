"""Batch-5: referral reward credit must be idempotent under duplicate execution."""
import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import UserPreference
from apps.growth.models import Referral, ReferralReward

User = get_user_model()


def _mk(username):
    u = User.objects.create(username=username, email=f"{username}@x.com")
    UserPreference.objects.get_or_create(user=u)
    return u


@pytest.mark.django_db
def test_duplicate_credit_run_single_transition():
    referrer = _mk("ref")
    referred = _mk("refd")
    referral = Referral.objects.create(referrer=referrer, referred_user=referred)
    reward = ReferralReward.objects.create(
        referrer=referrer, referral=referral,
        amount_cents=500, referred_purchase_amount_cents=1000,
        reward_percentage=10, status=ReferralReward.Status.PENDING)
    won1 = reward.mark_credited()
    stale = ReferralReward.objects.get(pk=reward.pk)
    stale.status = ReferralReward.Status.PENDING  # stale in-memory view (duplicate run)
    won2 = stale.mark_credited()
    assert won1 is True
    assert won2 is False
    reward.refresh_from_db()
    assert reward.status == ReferralReward.Status.CREDITED
