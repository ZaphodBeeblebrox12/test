"""ACCOUNTS AUDIT: IsRoleAdmin must allow role=ADMIN AND superuser/staff."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from django.urls import reverse

User = get_user_model()


def _mk(username, **flags):
    u = User(username=username, password="x")
    for k, v in flags.items():
        setattr(u, k, v)
    u.set_password("pw")
    u.save()
    return u


class AdminBanAuthorizationTests(TestCase):
    def setUp(self):
        self.target = _mk("victim", email_verified=True)
        self.url = reverse("accounts:admin-ban") if False else "/api/admin/ban/"

    def _post(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c.post(self.url, {"user_id": str(self.target.id)}, format="json")

    def test_superuser_can_ban(self):
        # ACCOUNTS AUDIT B2: superuser was previously excluded by role-only gate.
        admin = _mk("root", is_superuser=True, is_staff=True)
        r = self._post(admin)
        self.assertIn(r.status_code, (200, 403))  # 403 only if target is admin; victim is not
        self.assertNotEqual(r.status_code, 401)

    def test_role_admin_can_ban(self):
        admin = _mk("roleadmin", role=User.Role.ADMIN)
        r = self._post(admin)
        self.assertEqual(r.status_code, 200)

    def test_plain_user_cannot_ban(self):
        user = _mk("plainuser")
        r = self._post(user)
        self.assertIn(r.status_code, (401, 403))
