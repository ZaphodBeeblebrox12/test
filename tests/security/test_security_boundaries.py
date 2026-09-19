"""Batch-4A adversarial regression tests.

Protect the highest-value security/business boundaries confirmed in Batch 4.
Each test verifies BEHAVIOR (attack -> denied/invariant held), not implementation.
"""
import hmac
import hashlib
import json
import time
from datetime import timedelta
from django.utils import timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import UserPreference
from apps.api.models import APIKey
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.growth.models import GiftInvite
from apps.subscriptions.models import GiftSubscription, Plan, PlanPrice, Subscription

User = get_user_model()

import apps.bot_integration.signals as _sig


@pytest.fixture(autouse=True)
def _no_celery_broker(monkeypatch):
    """Subscription activation enqueues reconcile_user_access_task via Celery;
    under test there is no broker. Scope the patch per-test with monkeypatch so
    it auto-reverts and cannot leak into other test modules. Tests the claim
    invariant, not the queue transport."""
    monkeypatch.setattr(_sig, "reconcile_user_access_task",
                        type("T", (), {"delay": staticmethod(lambda *a, **k: None)}))


def make_user(username, **kw):
    kw.setdefault("email", f"{username}@x.com")
    u = User.objects.create(username=username, **kw)
    UserPreference.objects.get_or_create(user=u)
    return u


@pytest.mark.django_db
class TestIDOR:
    """User A must not operate on User B's objects."""

    def test_api_key_ownership(self):
        a, b = make_user("a"), make_user("b")
        key_b = APIKey.objects.create(user=b, name="b-key")
        client = APIClient()
        client.force_authenticate(a)
        # attempt to revoke B's key
        r = client.delete(f"/api/auth/keys/{key_b.id}/")
        assert r.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
        assert APIKey.objects.filter(id=key_b.id, is_active=True).exists()

    def test_notification_ownership(self):
        a, b = make_user("a"), make_user("b")
        n = Notification.objects.create(user=b, title="B secret", message="x")
        client = APIClient(); client.force_authenticate(a)
        r = client.post(f"/api/notifications/{n.id}/read/")
        assert r.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
        n.refresh_from_db()
        assert not getattr(n, "is_read", False)


@pytest.mark.django_db
class TestPrivilegeEscalation:
    """Privileged fields must remain server-controlled."""

    def test_profile_mass_assignment(self):
        u = make_user("esc")
        client = APIClient(); client.force_authenticate(u)
        r = client.patch("/api/auth/profile/", {
            "is_staff": True, "is_superuser": True,
            "is_staff_approved": True, "role": "admin",
        }, format="json")
        assert r.status_code in (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST)
        u.refresh_from_db()
        assert not u.is_staff
        assert not u.is_superuser
        assert getattr(u, "role", None) != getattr(User.Role, "ADMIN", object())


@pytest.mark.django_db
class TestAdminBoundary:
    """Admin endpoints require role=ADMIN; others are rejected."""

    def _post(self, user, path, data):
        c = APIClient()
        if user: c.force_authenticate(user)
        return c.post(path, data, format="json")

    def test_ban_anonymous_denied(self):
        r = self._post(None, "/api/admin/ban/", {"user_id": "x"})
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_ban_normal_user_denied(self):
        r = self._post(make_user("nu"), "/api/admin/ban/", {"user_id": "x"})
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_ban_admin_allowed(self):
        admin = make_user("adm", role=User.Role.ADMIN, is_superuser=True)
        target = make_user("tgt")
        r = self._post(admin, "/api/admin/ban/", {"user_id": str(target.id)})
        assert r.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestTelegramForgery:
    """Tampering any signed field must break auth."""

    def _hash(self, data, token):
        fields = sorted(f"{k}={v}" for k, v in data.items() if k != "hash" and v)
        dcs = "\n".join(fields)
        sk = hashlib.sha256(token.encode()).digest()
        return hmac.new(sk, dcs.encode(), hashlib.sha256).hexdigest()

    def test_modify_id_invalidates(self):
        token = "test_token"
        data = {"id": "100", "username": "alice", "auth_date": str(int(time.time()))}
        data["hash"] = self._hash(data, token)
        forged = dict(data); forged["id"] = "999"   # change id, keep old hash
        c = APIClient()
        r = c.post("/api/auth/telegram/", json.dumps(forged),
                   content_type="application/json")
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_modify_username_invalidates(self):
        token = "test_token"
        data = {"id": "100", "username": "alice", "auth_date": str(int(time.time()))}
        data["hash"] = self._hash(data, token)
        forged = dict(data); forged["username"] = "mallory"
        r = APIClient().post("/api/auth/telegram/", json.dumps(forged),
                             content_type="application/json")
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_valid_succeeds(self):
        token = "test_token"
        data = {"id": "100", "username": "alice", "auth_date": str(int(time.time()))}
        data["hash"] = self._hash(data, token)
        r = APIClient().post("/api/auth/telegram/", json.dumps(data),
                             content_type="application/json")
        assert r.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestGiftDoubleClaim:
    """Second claim must not produce a second credit."""

    def test_second_claim_rejected_no_double_credit(self):
        from apps.growth.services.gifts import GiftClaimService
        from apps.accounts.models import User as _U
        giver = make_user("giver")
        claimer = make_user("claimer", email="c@x.com")
        other = make_user("other")
        plan = Plan.objects.create(name="P", display_order=1)
        pp = PlanPrice.objects.create(plan=plan, interval="monthly", price_cents=100)
        gs = GiftSubscription.objects.create(
            from_user=giver, plan=plan, plan_price=pp,
            gift_code="tok123", to_user=None, expires_at=timezone.now() + timedelta(days=30))
        invite = GiftInvite.objects.create(
            gift_subscription=gs, claim_token="tok123",
            claim_token_hash=GiftInvite.hash_token("tok123"),
            recipient_email="c@x.com",
            expires_at=timezone.now() + timedelta(days=30))
        # first claim succeeds
        sub1 = GiftClaimService.claim_gift("tok123", claimer)
        invite.refresh_from_db()
        assert invite.status == GiftInvite.Status.CLAIMED
        # second claim (same or different user) must fail AND not double-extend
        from apps.growth.services.gifts import GiftAlreadyClaimedError
        with pytest.raises(GiftAlreadyClaimedError):
            GiftClaimService.claim_gift("tok123", claimer)
        invite.refresh_from_db()
        assert invite.claimed_by == claimer
        # only one resulting subscription
        assert Subscription.objects.filter(user=claimer).count() == 1
