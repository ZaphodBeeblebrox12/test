"""Audit regression tests: processor-level dispute lifecycle.

Locks the critical fix: funds_withdrawn does NOT confirm a chargeback,
and closed/lost revokes exactly once across BOTH layers (processor +
dispute service) without double cancellation or double notification.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.disputes.models import Dispute
from apps.payments.models import PaymentIntent, WebhookEvent
from apps.subscriptions.models import Plan, PlanPrice, Subscription

from .webhook_processor import process_webhook_event

User = get_user_model()


def _fixture():
    user = User.objects.create_user(username="w", password="p")
    plan = Plan.objects.create(name="Pro", is_active=True)
    pp = PlanPrice.objects.create(plan=plan, interval="monthly",
                                  price_cents=4900, currency="USD")
    intent = PaymentIntent.objects.create(
        user=user, plan=plan, plan_price=pp, amount=4900, currency="USD",
        provider="stripe", status="success", provider_reference="cs_1",
        provider_payment_id="pi_1")
    sub = Subscription.objects.create(
        user=user, plan=plan, status="active", is_active=True,
        started_at=timezone.now(),
        expires_at=timezone.now() + timezone.timedelta(days=30),
        price_cents=4900, price_currency="USD")
    return user, plan, intent, sub


class _Job:
    def __init__(self):
        self.failed = None
    def mark_failed(self, error=""):
        self.failed = error


def _event(et, obj, eid="evt_1"):
    return WebhookEvent.objects.create(
        provider="stripe", provider_event_id=eid, event_type=et,
        payload={"type": et, "data": {"object": obj}}, status="received")


def _run(e):
    """The processor looks the durable event up by its UUID primary key."""
    process_webhook_event({"webhook_event_id": str(e.pk)}, _Job())


class ProcessorDisputeTests(TestCase):
    # AUDIT C1: funds_withdrawn must NOT confirm (case still winnable).
    def test_funds_withdrawn_does_not_revoke(self):
        user, plan, intent, sub = _fixture()
        _run(_event("charge.dispute.funds_withdrawn",
                    {"id": "dp_1", "payment_intent": "pi_1", "charge": "ch_1",
                     "amount": 4900, "currency": "usd",
                     "status": "under_review", "reason": "fraudulent",
                     "evidence_details": {"due_by": 1700000000}}, "evt_fw1"))
        with patch("apps.subscriptions.services.cancel_subscription") as c:
            _run(_event("charge.dispute.funds_withdrawn",
                        {"id": "dp_1", "payment_intent": "pi_1",
                         "charge": "ch_1", "amount": 4900,
                         "currency": "usd", "status": "under_review",
                         "reason": "fraudulent"}, "evt_fw2"))
        assert not c.called
        sub.refresh_from_db()
        assert sub.is_active and sub.status == "active"
        intent.refresh_from_db()
        assert intent.chargeback is True            # flagged
        assert intent.chargeback_confirmed is False  # NOT confirmed
        d = Dispute.objects.get(provider_dispute_id="dp_1")
        assert d.status == "opened"
        assert d.funds_withdrawn_at is not None

    # closed/lost confirms exactly once; second delivery is a no-op.
    def test_closed_lost_revokes_once_across_both_layers(self):
        user, plan, intent, sub = _fixture()
        obj = {"id": "dp_2", "payment_intent": "pi_1", "charge": "ch_1",
               "amount": 4900, "currency": "usd", "reason": "fraudulent"}
        with patch("apps.payments.notifications.notify_chargedback") as n:
            _run(_event("charge.dispute.created",
                        {**obj, "status": "needs_response"}, "evt_a"))
            assert not n.called  # opened: no revocation, no notice
            _run(_event("charge.dispute.closed",
                        {**obj, "status": "lost"}, "evt_b"))
            assert n.call_count == 1  # exactly one customer notice
        sub.refresh_from_db()
        assert not sub.is_active and sub.status == "canceled"
        intent.refresh_from_db()
        assert intent.chargeback_confirmed is True
        d = Dispute.objects.get(provider_dispute_id="dp_2")
        assert d.status == "lost" and d.access_actioned is True
        with patch("apps.payments.notifications.notify_chargedback") as n2:
            _run(_event("charge.dispute.closed",
                        {**obj, "status": "lost"}, "evt_c"))
            assert not n2.called
        assert d.events.filter(event_type="access_action").count() == 1

    # closed/won clears flags, entitlement untouched.
    def test_closed_won_clears_flags(self):
        user, plan, intent, sub = _fixture()
        obj = {"id": "dp_3", "payment_intent": "pi_1", "charge": "ch_1",
               "amount": 4900, "currency": "usd", "reason": "fraudulent"}
        _run(_event("charge.dispute.funds_withdrawn",
                    {**obj, "status": "under_review"}, "evt_d"))
        _run(_event("charge.dispute.closed",
                    {**obj, "status": "won"}, "evt_e"))
        intent.refresh_from_db()
        assert intent.chargeback is False
        assert intent.chargeback_confirmed is False
        sub.refresh_from_db()
        assert sub.is_active  # never revoked while the case was open

    # out-of-order: closed/lost arrives BEFORE created (replay). Unique
    # constraint on (provider, provider_dispute_id) keeps one case.
    def test_out_of_order_created_after_closed(self):
        user, plan, intent, sub = _fixture()
        obj = {"id": "dp_4", "payment_intent": "pi_1", "charge": "ch_1",
               "amount": 4900, "currency": "usd", "reason": "fraudulent"}
        _run(_event("charge.dispute.closed",
                    {**obj, "status": "lost"}, "evt_f"))
        _run(_event("charge.dispute.created",
                    {**obj, "status": "lost"}, "evt_g"))
        assert Dispute.objects.filter(
            provider_dispute_id="dp_4").count() == 1
        d = Dispute.objects.get(provider_dispute_id="dp_4")
        assert d.status == "lost"
