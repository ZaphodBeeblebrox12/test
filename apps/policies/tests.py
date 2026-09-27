from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import PolicyAcceptance, PolicyVersion
from .services import publish, record_checkout_acceptances

User = get_user_model()


def _pv(pt="terms", ver="1.0", status="active", content="<p>x</p>"):
    # The seed migration creates ACTIVE v1.0 rows in every test database;
    # replace deterministically instead of colliding with it.
    PolicyVersion.objects.filter(policy_type=pt, version=ver).delete()
    return PolicyVersion.objects.create(
        policy_type=pt, version=ver, title=pt, content_html=content,
        status=status, effective_from=timezone.now(),
        published_at=timezone.now() if status == "active" else None)


class PolicyVersionTests(TestCase):
    def test_content_hash_matches_content(self):
        pv = _pv(ver="9.9", status="draft")
        import hashlib
        assert pv.content_sha256 == hashlib.sha256(b"<p>x</p>").hexdigest()

    def test_unique_type_version(self):
        _pv()
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            PolicyVersion.objects.create(
                policy_type="terms", version="1.0", title="T2",
                content_html="<p>y</p>", status="draft",
                effective_from=timezone.now())

    def test_publish_swaps_single_active(self):
        a = _pv(status="active")
        b = PolicyVersion.objects.create(
            policy_type="terms", version="2.0", title="t2",
            content_html="<p>y</p>", status="draft",
            effective_from=timezone.now())
        publish(b)
        a.refresh_from_db(); b.refresh_from_db()
        assert a.status == "archived" and b.status == "active"
        assert b.published_at is not None

    def test_no_second_active_per_type(self):
        _pv(status="active")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            _pv(ver="2.0", status="active")


class PolicyAcceptanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.pv = _pv()

    def test_acceptance_immutable(self):
        acc = PolicyAcceptance.objects.create(
            user=self.user, policy_version=self.pv, accepted_at=timezone.now(),
            ip_address="127.0.0.1")
        with self.assertRaises(RuntimeError):
            acc.accepted_at = timezone.now()
            acc.save()

    def test_checkout_records_granular_acceptances_from_one_action(self):
        for pt in ("refund", "risk"):
            _pv(pt=pt)
        class R:
            META = {"REMOTE_ADDR": "10.0.0.1", "HTTP_USER_AGENT": "UA"}
            class session:
                session_key = "s1"
        accs, missing = record_checkout_acceptances(self.user, R())
        assert len(accs) == 3 and not missing
        assert {a.policy_version.policy_type for a in accs} == {
            "terms", "refund", "risk"}

    def test_acceptance_carries_session_ref_and_ip(self):
        class R:
            META = {"REMOTE_ADDR": "10.0.0.9", "HTTP_USER_AGENT": "UA"}
            class session:
                session_key = "sess-1"
        acc = record_checkout_acceptances(self.user, R())[0][0]
        assert acc.ip_address == "10.0.0.9" and len(acc.session_ref) == 64
