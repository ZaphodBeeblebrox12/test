from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.payments.models import PaymentIntent
from apps.policies.models import PolicyAcceptance, PolicyVersion
from apps.subscriptions.models import Plan, PlanPrice

from .fingerprint import compute_fingerprint
from .models import CheckoutEvidence
from .services import capture_checkout_evidence

User = get_user_model()


def _seed_policies():
    PolicyVersion.objects.filter(policy_type__in=("terms", "refund", "risk", "cancellation", "privacy"), version="1.0").delete()
    for pt in ("terms", "refund", "risk", "cancellation", "privacy"):
        PolicyVersion.objects.create(
            policy_type=pt, version="1.0", title=pt, content_html="<p>x</p>",
            status="active", effective_from=timezone.now(),
            published_at=timezone.now())


def _rf(user, ip="203.0.113.7"):
    rf = RequestFactory()
    req = rf.post("/x/")
    req.user = user
    req.session = type("S", (), {"session_key": "abc"})()
    req.META["REMOTE_ADDR"] = ip
    req.META["HTTP_USER_AGENT"] = "TestAgent/1.0"
    return req


class FingerprintTests(TestCase):
    def test_deterministic_and_versioned(self):
        sig = {"user_agent": "UA", "language": "en", "timezone": "UTC",
               "screen": "1920x1080", "platform": "Linux"}
        a, ca = compute_fingerprint(sig)
        b, cb = compute_fingerprint(dict(sig))
        assert a == b and len(a) == 64 and ca == cb

    def test_no_sensitive_inputs_accepted(self):
        fp, canon = compute_fingerprint({"user_agent": "x",
                                         "mac_address": "aa:bb",
                                         "canvas": "junk"})
        assert "mac_address" not in canon and "canvas" not in canon


class CaptureTests(TestCase):
    def setUp(self):
        _seed_policies()
        self.user = User.objects.create_user(username="u", password="p")
        self.plan = Plan.objects.create(name="Pro", is_active=True)
        self.pp = PlanPrice.objects.create(
            plan=self.plan, interval="monthly", price_cents=4900,
            currency="USD", is_active=True)
        self.intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, plan_price=self.pp, amount=4900,
            currency="USD", provider="stripe",
            status=PaymentIntent.Status.PENDING, country="US")

    def test_capture_records_granular_acceptances_from_one_moment(self):
        req = _rf(self.user)
        ev = capture_checkout_evidence(
            self.intent, req,
            client_signals={"user_agent": "TestAgent/1.0", "timezone": "UTC"})
        assert ev.ip_address == "203.0.113.7"
        assert ev.terms_version.version == "1.0"
        # ONE agreement moment -> granular, individually-versioned records:
        accs = PolicyAcceptance.objects.filter(user=self.user,
                                               checkout_evidence=ev)
        assert {a.policy_version.policy_type for a in accs} == {
            "terms", "refund", "risk"}  # single-checkbox doctrine: exactly 3

    def test_capture_idempotent_under_double_submit(self):
        ev = capture_checkout_evidence(self.intent, _rf(self.user),
                                       client_signals={})
        ev2 = capture_checkout_evidence(self.intent, _rf(self.user),
                                        client_signals={})
        assert ev2.pk == ev.pk  # first write wins; no IntegrityError

    def test_capture_refuses_without_active_policy(self):
        PolicyVersion.objects.filter(policy_type="risk").update(
            status="archived")
        with self.assertRaises(RuntimeError):
            capture_checkout_evidence(self.intent, _rf(self.user),
                                      client_signals={})

    def test_evidence_immutable(self):
        ev = capture_checkout_evidence(self.intent, _rf(self.user),
                                       client_signals={})
        ev.ip_address = "1.2.3.4"
        with self.assertRaises(RuntimeError):
            ev.save()


class TrialSnapshotTests(TestCase):
    def setUp(self):
        _seed_policies()
        self.user = User.objects.create_user(username="t", password="p")
        self.plan = Plan.objects.create(name="Trial", is_active=True,
                                        is_trial=True, trial_duration_days=7)
        self.pp = PlanPrice.objects.create(
            plan=self.plan, interval="monthly", price_cents=700,
            currency="USD")
        PlanRefundTerms = __import__(
            "apps.evidence.models", fromlist=["PlanRefundTerms"]).PlanRefundTerms
        PlanRefundTerms.objects.create(
            plan=self.plan, refund_window_days=3,
            trial_refund_window_days=1,
            cancellation_deadline_hours=24,
            refund_terms_text="Refundable within 1 day of trial start.")

    def test_trial_snapshot_freezes_terms(self):
        intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, plan_price=self.pp, amount=700,
            base_amount_cents=4900, currency="USD", provider="stripe",
            status="pending")
        ev = capture_checkout_evidence(intent, _rf(self.user),
                                       client_signals={})
        snap = ev.pricing_snapshot
        assert snap["is_trial"] is True
        assert snap["trial_duration_days"] == 7
        assert snap["conversion_price_cents"] == 4900
        assert snap["conversion_disclosure"]
        assert snap["refund_terms"]["trial_refund_window_days"] == 1
        # Changing live config afterwards must NOT change the snapshot:
        intent.plan.refund_terms.trial_refund_window_days = 30
        intent.plan.refund_terms.save()
        ev.refresh_from_db()
        assert ev.pricing_snapshot["refund_terms"][
            "trial_refund_window_days"] == 1
