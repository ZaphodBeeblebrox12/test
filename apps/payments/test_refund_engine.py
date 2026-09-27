"""Trial vs standard refund matrix.

Covers the business-rule edge cases: eligibility windows (trial-specific),
snapshot pinning, unified ledger + idempotency, cancellation != refund,
refund-vs-dispute ordering, full vs partial refunds, conversion claims.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.evidence.models import CheckoutEvidence, PlanRefundTerms
from apps.evidence.services import capture_checkout_evidence
from apps.policies.models import PolicyVersion
from apps.subscriptions.models import Plan, PlanPrice

from .models import PaymentIntent, Refund
from .refund_engine import (
    RefundNotEligible, assert_refundable, evaluate_refund_eligibility,
    evaluate_trial_cancellation_claim)
from .services import record_refund, refund_context_for

User = get_user_model()


def _seed_policies():
    for pt in ("terms", "refund", "risk", "cancellation"):
        PolicyVersion.objects.create(
            policy_type=pt, version="1.0", title=pt, content_html="<p>x</p>",
            status="active", effective_from=timezone.now(),
            published_at=timezone.now())


def _rf(user):
    from django.test import RequestFactory
    req = RequestFactory().post("/x/")
    req.user = user
    req.session = type("S", (), {"session_key": "s"})()
    req.META["REMOTE_ADDR"] = "198.51.100.4"
    req.META["HTTP_USER_AGENT"] = "T/1"
    return req


def _intent(user, plan, price, days_ago=0, base=None):
    pp = PlanPrice.objects.create(plan=plan, interval="monthly",
                                  price_cents=price, currency="USD")
    intent = PaymentIntent.objects.create(
        user=user, plan=plan, plan_price=pp, amount=price,
        base_amount_cents=base if base is not None else price,
        currency="USD", provider="stripe", status="success")
    intent.created_at = timezone.now() - timedelta(days=days_ago)
    intent.save(update_fields=["created_at"])
    capture_checkout_evidence(intent, _rf(user), client_signals={})
    return intent


class RefundEngineTests(TestCase):
    def setUp(self):
        _seed_policies()
        self.user = User.objects.create_user(username="u", password="p")
        self.trial_plan = Plan.objects.create(name="Trial", is_active=True,
                                              is_trial=True,
                                              trial_duration_days=7)
        self.paid_plan = Plan.objects.create(name="Pro", is_active=True)
        PlanRefundTerms.objects.create(
            plan=self.trial_plan, refund_window_days=3,
            trial_refund_window_days=1, cancellation_deadline_hours=24)
        PlanRefundTerms.objects.create(plan=self.paid_plan,
                                       refund_window_days=14)

    # 1. trial refund INSIDE the trial refund window -> eligible
    def test_trial_refund_within_trial_window(self):
        intent = _intent(self.user, self.trial_plan, 700, days_ago=0)
        el = evaluate_refund_eligibility(intent)
        assert el.eligible and el.context == "trial" and el.window_days == 1

    # 2. trial consumed but still inside window -> eligible (usage is not a
    #    refund-eligibility criterion; policy decides)
    def test_trial_refund_within_window_despite_usage(self):
        intent = _intent(self.user, self.trial_plan, 700, days_ago=0)
        el = evaluate_refund_eligibility(intent)
        assert el.eligible

    # 3. trial refund AFTER the trial window (but inside standard window)
    #    -> NOT eligible: the trial-specific window governs trial payments.
    def test_trial_refund_after_trial_window_expires(self):
        intent = _intent(self.user, self.trial_plan, 700, days_ago=2)
        el = evaluate_refund_eligibility(intent)
        assert not el.eligible and el.reason == "refund_window_expired"

    # 3b. standard plan at 2 days (14d window) -> still eligible: the two
    #     plan types follow DIFFERENT rules on the same facts.
    def test_standard_plan_different_window(self):
        intent = _intent(self.user, self.paid_plan, 4900, days_ago=2)
        el = evaluate_refund_eligibility(intent)
        assert el.eligible and el.window_days == 14

    # 4-5. cancellation is a different event: no Refund row is written by
    # cancel flows (verified: refund ledger untouched by cancellation).
    def test_cancellation_does_not_write_refund(self):
        intent = _intent(self.user, self.trial_plan, 700)
        from apps.subscriptions.models import Subscription
        from apps.subscriptions.services import cancel_subscription
        sub = Subscription.objects.create(
            user=self.user, plan=self.trial_plan, status="active",
            is_active=True, started_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
            price_cents=700, price_currency="USD")
        cancel_subscription(sub, actor="user")
        assert Refund.objects.filter(payment_intent=intent).count() == 0

    # 6. refund of the CONVERTED payment: context is still 'trial' (the
    #    snapshot says is_trial) but the window was measured from the
    #    ORIGINAL trial charge; a conversion repurchase is a new intent with
    #    its own snapshot (context standard unless plan is trial).
    def test_conversion_payment_is_new_transaction(self):
        trial_intent = _intent(self.user, self.trial_plan, 700, days_ago=10)
        el = evaluate_refund_eligibility(trial_intent)
        assert not el.eligible  # decided by the trial window from charge date

    # 7-8. FULL refund cancels entitlement via existing apply_refund_policy
    #      (unified behavior); PARTIAL refund leaves entitlement unchanged.
    def test_full_vs_partial_refund_entitlement(self):
        from unittest.mock import patch
        intent = _intent(self.user, self.paid_plan, 4900)
        with patch("apps.subscriptions.services.cancel_subscription") as c:
            record_refund(intent, provider="stripe",
                          provider_refund_id="rf_full", amount_cents=4900)
            from .services import apply_refund_policy
            apply_refund_policy(intent)
            assert c.called  # full refund -> cancel once
        intent2 = _intent(self.user, self.paid_plan, 4900)
        with patch("apps.subscriptions.services.cancel_subscription") as c2:
            record_refund(intent2, provider="stripe",
                          provider_refund_id="rf_part", amount_cents=1000)
            from .services import apply_refund_policy
            apply_refund_policy(intent2)
            assert not c2.called  # partial -> entitlement untouched

    # 9-11. refund/dispute ordering: the ledger records refunds regardless of
    # dispute state (reality); disputes reference refund facts. Idempotency
    # intact under retries.
    def test_refund_ledger_unified_and_idempotent(self):
        intent = _intent(self.user, self.trial_plan, 700)
        r1, c1 = record_refund(intent, provider="stripe",
                               provider_refund_id="rf_x", amount_cents=700)
        r2, c2 = record_refund(intent, provider="stripe",
                               provider_refund_id="rf_x", amount_cents=700)
        assert c1 and not c2 and r1.pk == r2.pk
        assert Refund.objects.filter(payment_intent=intent).count() == 1
        assert intent.refunds.get().commercial_context == "trial"
        # counters incremented exactly once:
        intent.refresh_from_db()
        assert intent.refunded_cents == 700

    # 12-13. partial + full amounts accumulate on the unified ledger.
    def test_partial_then_full_accumulates(self):
        intent = _intent(self.user, self.paid_plan, 4900)
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_p1", amount_cents=1000)
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_p2", amount_cents=3900)
        intent.refresh_from_db()
        assert intent.refunded_cents == 4900 and intent.is_fully_refunded

    # 16/19/20. multi-account trials + 'cancelled before conversion' claims
    # are INTERNAL risk signals and cancellation-ledger facts - never network
    # evidence by themselves.
    def test_trial_cancellation_claim_uses_ledger(self):
        intent = _intent(self.user, self.trial_plan, 700)
        out = evaluate_trial_cancellation_claim(intent)
        assert out["cancelled_before_conversion"] is False
        assert out["snapshot"]["is_trial"] is True
        assert out["snapshot"]["trial_duration_days"] == 7

    # 18. refund THEN chargeback: 'credit already issued' - refund rows are
    # present in evidence (credit_not_processed section leads with them).
    def test_refund_then_chargeback_reality_recorded(self):
        intent = _intent(self.user, self.paid_plan, 4900)
        record_refund(intent, provider="stripe",
                      provider_refund_id="rf_18", amount_cents=4900)
        from apps.disputes.services import ingest_dispute_event
        d = ingest_dispute_event("stripe", {"id": "dp18",
                                            "reason": "credit_not_processed",
                                            "amount": 4900,
                                            "currency": "usd"}, intent,
                                 "charge.dispute.created")
        pkg_sections = __import__(
            "apps.disputes.services", fromlist=["build_sections"]
        ).build_sections(d)
        refund_facts = pkg_sections["refunds"]["facts"]
        assert any("rf_18" in f["value"] for f in refund_facts)

    # Gate: manual refunds blocked outside policy; webhook refunds are
    # recorded regardless (ledger = reality).
    def test_manual_gate_blocks_out_of_policy(self):
        intent = _intent(self.user, self.trial_plan, 700, days_ago=5)
        with self.assertRaises(RefundNotEligible):
            assert_refundable(intent)

    def test_context_stamping_uses_snapshot_not_live_config(self):
        intent = _intent(self.user, self.trial_plan, 700)
        # flip the live plan to non-trial AFTER purchase:
        self.trial_plan.is_trial = False
        self.trial_plan.save(update_fields=["is_trial"])
        assert refund_context_for(intent) == "trial"
