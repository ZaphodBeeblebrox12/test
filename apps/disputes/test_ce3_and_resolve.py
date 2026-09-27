from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.evidence.models import CheckoutEvidence, PaymentAuthentication
from apps.payments.models import PaymentIntent
from apps.policies.models import PolicyVersion
from apps.subscriptions.models import Plan, PlanPrice

from . import services
from .models import Dispute

User = get_user_model()


def _seed():
    PolicyVersion.objects.filter(policy_type__in=("terms", "refund", "risk", "cancellation", "privacy"), version="1.0").delete()
    for pt in ("terms", "refund", "risk"):
        PolicyVersion.objects.create(policy_type=pt, version="1.0", title=pt,
            content_html="<p>x</p>", status="active",
            effective_from=timezone.now(), published_at=timezone.now())


def _intent(user, days_ago, fp, card_fp):
    plan = Plan.objects.create(name=f"P{days_ago}{fp[:4]}",
        tier=f"t{fp[:6]}-{days_ago}", is_active=True)
    pp = PlanPrice.objects.create(plan=plan, interval="monthly",
                                  price_cents=100, currency="USD")
    intent = PaymentIntent.objects.create(
        user=user, plan=plan, plan_price=pp, amount=100, currency="USD",
        provider="stripe", status="success")
    intent.created_at = timezone.now() - timedelta(days=days_ago)
    intent.save(update_fields=["created_at"])
    t = PolicyVersion.objects.get(policy_type="terms", version="1.0")
    r = PolicyVersion.objects.get(policy_type="refund", version="1.0")
    k = PolicyVersion.objects.get(policy_type="risk", version="1.0")
    CheckoutEvidence.objects.create(
        payment_intent=intent, ip_address="10.0.0.1", user_agent="UA",
        device_fingerprint=fp, session_ref="s", pricing_snapshot={},
        terms_version=t, refund_policy_version=r, risk_disclaimer_version=k,
        accepted_at=intent.created_at)
    PaymentAuthentication.objects.create(
        payment_intent=intent, card_fingerprint=card_fp,
        three_ds_result="authenticated")
    return intent


class CE3RegressionTests(TestCase):
    def setUp(self):
        _seed()
        self.user = User.objects.create_user(username="c", password="p")

    def _dispute_on(self, intent, opened_days_ago=0):
        return Dispute.objects.create(
            provider="stripe", provider_dispute_id=f"dp_{intent.pk}",
            payment_intent=intent,
            opened_at=timezone.now() - timedelta(days=opened_days_ago))

    def test_submitted_dispute_excluded_from_qualifying_history(self):
        """AUDIT C2: a SUBMITTED (undecided) dispute must not count as
        'undisputed' history for CE3 matching."""
        prior = _intent(self.user, 200, "fp1", "cardX")
        d = self._dispute_on(prior, opened_days_ago=0)
        services._transition(d, Dispute.Status.SUBMITTED)
        current = _intent(self.user, 0, "fp1", "cardX")
        cur_disp = self._dispute_on(current)
        assert services.ce3_matches(cur_disp) == []  # prior excluded

    def test_won_dispute_counts_as_undisputed_history(self):
        prior = _intent(self.user, 200, "fp2", "cardY")
        d = self._dispute_on(prior)
        services._transition(d, Dispute.Status.WON)
        current = _intent(self.user, 0, "fp2", "cardY")
        cur_disp = self._dispute_on(current)
        matches = services.ce3_matches(cur_disp)
        assert len(matches) == 1
        assert "device_fingerprint" in matches[0]["matched_elements"]

    def test_confirmed_chargeback_excluded_even_without_dispute_row(self):
        prior = _intent(self.user, 200, "fp3", "cardZ")
        PaymentIntent.objects.filter(pk=prior.pk).update(
            chargeback_confirmed=True)
        current = _intent(self.user, 0, "fp3", "cardZ")
        cur_disp = self._dispute_on(current)
        assert services.ce3_matches(cur_disp) == []


class ManualResolveTests(TestCase):
    def setUp(self):
        _seed()
        self.user = User.objects.create_user(username="m", password="p")
        self.intent = _intent(self.user, 0, "fp9", "card9")

    def test_manual_won_clears_payment_flags(self):
        PaymentIntent.objects.filter(pk=self.intent.pk).update(
            chargeback=True, chargeback_confirmed=False)
        d = Dispute.objects.create(provider="stripe",
                                   provider_dispute_id="dp_mw",
                                   payment_intent=self.intent)
        from django.contrib.auth import get_user_model
        admin = User.objects.create_user(username="adm", password="p",
                                         is_staff=True)
        services.resolve_manually(d, Dispute.Status.WON, actor=admin,
                                  note="issuer reversal")
        self.intent.refresh_from_db()
        assert self.intent.chargeback is False
        assert d.events.filter(to_status="won").exists()

    def test_manual_lost_revokes_once(self):
        d = Dispute.objects.create(provider="stripe",
                                   provider_dispute_id="dp_ml",
                                   payment_intent=self.intent)
        admin = User.objects.create_user(username="adm2", password="p",
                                         is_staff=True)
        with patch("apps.payments.notifications.notify_chargedback"):
            services.resolve_manually(d, Dispute.Status.LOST, actor=admin)
        services._action_access(d)  # retry: no-op
        d.refresh_from_db()
        assert d.access_actioned is True
        assert d.events.filter(event_type="access_action").count() == 1
        self.intent.refresh_from_db()
        assert self.intent.chargeback_confirmed is True
