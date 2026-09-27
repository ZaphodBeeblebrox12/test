"""AUDIT section 11: refund/chargeback interaction ordering."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.disputes.services import ingest_dispute_event
from apps.evidence.models import CheckoutEvidence
from apps.payments.models import PaymentIntent, Refund
from apps.policies.models import PolicyVersion
from apps.subscriptions.models import Plan, PlanPrice

from .services import apply_refund_policy, record_refund

User = get_user_model()


def _seed():
    for pt in ("terms", "refund", "risk"):
        PolicyVersion.objects.create(policy_type=pt, version="1.0", title=pt,
            content_html="<p>x</p>", status="active",
            effective_from=timezone.now(), published_at=timezone.now())


def _intent(user, amount=4900):
    plan = Plan.objects.create(name=f"Plan{user.username}", is_active=True)
    pp = PlanPrice.objects.create(plan=plan, interval="monthly",
                                  price_cents=amount, currency="USD")
    intent = PaymentIntent.objects.create(
        user=user, plan=plan, plan_price=pp, amount=amount, currency="USD",
        provider="stripe", status="success")
    t = PolicyVersion.objects.get(policy_type="terms", version="1.0")
    r = PolicyVersion.objects.get(policy_type="refund", version="1.0")
    k = PolicyVersion.objects.get(policy_type="risk", version="1.0")
    CheckoutEvidence.objects.create(
        payment_intent=intent, ip_address="10.0.0.1", user_agent="UA",
        device_fingerprint=f"fp-{user.username}", session_ref="s",
        pricing_snapshot={}, terms_version=t, refund_policy_version=r,
        risk_disclaimer_version=k, accepted_at=timezone.now())
    return intent


class RefundDisputeInteractionTests(TestCase):
    def setUp(self):
        _seed()
        self.user = User.objects.create_user(username="rd", password="p")

    # refund BEFORE dispute: ledger intact, dispute shows credit-issued.
    def test_refund_then_dispute(self):
        intent = _intent(self.user)
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_1", amount_cents=4900)
        d = ingest_dispute_event("stripe", {"id": "dp_r1",
            "reason": "credit_not_processed", "amount": 4900,
            "currency": "usd"}, intent, "charge.dispute.created")
        assert intent.refunds.filter(provider_refund_id="rf_1").exists()
        # package: refunds section leads with the refund fact
        from apps.disputes.services import build_sections
        secs = build_sections(d)
        assert any("rf_1" in f["value"] for f in secs["refunds"]["facts"])

    # dispute BEFORE refund: refund recorded while case open (reality),
    # no entitlement change from the open case.
    def test_dispute_open_then_refund(self):
        intent = _intent(self.user)
        d = ingest_dispute_event("stripe", {"id": "dp_r2",
            "reason": "fraudulent", "amount": 4900, "currency": "usd"},
            intent, "charge.dispute.created")
        from apps.subscriptions.models import Subscription
        sub = Subscription.objects.create(
            user=self.user, plan=intent.plan, status="active",
            is_active=True, started_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=30),
            price_cents=4900, price_currency="USD")
        with patch("apps.subscriptions.services.cancel_subscription") as c:
            record_refund(intent, provider="stripe",
                          provider_refund_id="rf_2", amount_cents=4900)
            apply_refund_policy(intent)   # full refund -> cancel via refund path
            assert c.call_count == 1
        d.refresh_from_db()
        assert d.status == "opened"  # open case unaffected by refund

    # chargeback on a FULLY-refunded transaction: no double revocation,
    # flags still set (financial fact), access already gone via refund path.
    def test_chargeback_on_fully_refunded_txn(self):
        intent = _intent(self.user)
        from apps.subscriptions.models import Subscription
        sub = Subscription.objects.create(
            user=self.user, plan=intent.plan, status="active",
            is_active=True, started_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=30),
            price_cents=4900, price_currency="USD")
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_3", amount_cents=4900)
        apply_refund_policy(intent)
        sub.refresh_from_db()
        assert not sub.is_active  # refund path canceled it
        d = ingest_dispute_event("stripe", {"id": "dp_r3",
            "reason": "fraudulent", "amount": 4900, "currency": "usd"},
            intent, "charge.dispute.closed")
        ingest_dispute_event("stripe", {"id": "dp_r3",
            "reason": "fraudulent", "amount": 4900, "currency": "usd",
            "status": "lost"}, intent, "charge.dispute.closed")
        # exactly one access_action; cancel not re-run by chargeback layer
        d.refresh_from_db()
        assert d.events.filter(event_type="access_action").count() == 1
        self.assertFalse(d.access_actioned is False)

    # partial refunds accumulate; full chargeback later revokes once.
    def test_partial_refunds_then_chargeback(self):
        intent = _intent(self.user)
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_p1", amount_cents=1000)
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_p2", amount_cents=2000)
        intent.refresh_from_db()
        assert intent.refunded_cents == 3000 and intent.is_partially_refunded
        d = ingest_dispute_event("stripe", {"id": "dp_r4",
            "reason": "fraudulent", "amount": 4900, "currency": "usd"},
            intent, "charge.dispute.created")
        ingest_dispute_event("stripe", {"id": "dp_r4",
            "reason": "fraudulent", "amount": 4900, "currency": "usd",
            "status": "lost"}, intent, "charge.dispute.closed")
        d.refresh_from_db()
        assert d.access_actioned is True
        intent.refresh_from_db()
        assert intent.chargeback_confirmed is True
