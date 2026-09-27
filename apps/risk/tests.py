from django.test import TestCase

from apps.evidence.models import CheckoutEvidence, PaymentAuthentication
from apps.payments.models import PaymentIntent
from apps.policies.models import PolicyVersion
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import RiskSignal
from .services import detect_shared_credentials, flag_prior_dispute

User = get_user_model()


def _seed():
    PolicyVersion.objects.filter(policy_type__in=("terms", "refund", "risk", "cancellation", "privacy"), version="1.0").delete()
    for pt in ("terms", "refund", "risk"):
        PolicyVersion.objects.create(policy_type=pt, version="1.0", title=pt,
            content_html="<p>x</p>", status="active",
            effective_from=timezone.now(), published_at=timezone.now())


def _intent(user, amount=4900):
    from apps.subscriptions.models import Plan, PlanPrice
    plan = Plan.objects.create(name=f"P{user.username}",
        tier=f"t-{user.username}", is_active=True)
    pp = PlanPrice.objects.create(plan=plan, interval="monthly",
                                  price_cents=amount, currency="USD")
    return PaymentIntent.objects.create(user=user, plan=plan, plan_price=pp,
        amount=amount, currency="USD", provider="stripe", status="success")


class RiskSignalTests(TestCase):
    def setUp(self):
        _seed()
        self.u1 = User.objects.create_user(username="a", password="p")
        self.u2 = User.objects.create_user(username="b", password="p")

    def _evidence(self, intent, fp):
        t = PolicyVersion.objects.get(policy_type="terms", version="1.0")
        r = PolicyVersion.objects.get(policy_type="refund", version="1.0")
        k = PolicyVersion.objects.get(policy_type="risk", version="1.0")
        return CheckoutEvidence.objects.create(
            payment_intent=intent, ip_address="10.0.0.1", user_agent="UA",
            device_fingerprint=fp, session_ref="s", pricing_snapshot={},
            terms_version=t, refund_policy_version=r,
            risk_disclaimer_version=k, accepted_at=timezone.now())

    def test_shared_device_detected(self):
        i1, i2 = _intent(self.u1), _intent(self.u2)
        self._evidence(i1, "fp-shared")
        self._evidence(i2, "fp-shared")
        detect_shared_credentials()
        sig = RiskSignal.objects.get(
            signal_type=RiskSignal.SignalType.MULTI_ACCOUNT_DEVICE)
        assert sig.subject_hash == "fp-shared" and sig.severity == 3

    def test_prior_dispute_flag(self):
        from apps.disputes.models import Dispute
        i = _intent(self.u1)
        d = Dispute.objects.create(provider="stripe",
                                   provider_dispute_id="dp_x",
                                   payment_intent=i)
        flag_prior_dispute(d)
        assert RiskSignal.objects.filter(
            signal_type=RiskSignal.SignalType.PRIOR_DISPUTE,
            user=self.u1).exists()

    def test_detection_idempotent(self):
        i1, i2 = _intent(self.u1), _intent(self.u2)
        self._evidence(i1, "fp-shared2")
        self._evidence(i2, "fp-shared2")
        detect_shared_credentials()
        detect_shared_credentials()
        assert RiskSignal.objects.filter(
            subject_hash="fp-shared2").count() == 1
