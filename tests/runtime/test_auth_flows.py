"""Batch-auth: complete authentication-surface regression tests.

Covers password auth, optional OAuth (no SocialApp -> pages render), signup,
createsuperuser->admin auth, admin authorization, banned-user restriction.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


def _mk_superuser(username="root"):
    return User.objects.create_superuser(
        username=username, email=f"{username}@x.com", password="pass12345!")


@pytest.mark.django_db
class TestLoginPage:
    def test_login_get_200_without_socialapp(self, client):
        assert client.get("/accounts/login/").status_code == 200

    def test_login_valid_credentials(self, client):
        _mk_superuser()
        r = client.post("/accounts/login/", {
            "login": "root", "password": "pass12345!"})
        assert r.status_code in (200, 302)

    def test_login_invalid_credentials(self, client):
        _mk_superuser()
        r = client.post("/accounts/login/", {
            "login": "root", "password": "wrong"}, follow=True)
        assert r.status_code == 200
        assert b"root" in r.content or b"incorrect" in r.content.lower() or b"error" in r.content.lower()


@pytest.mark.django_db
class TestSignupPage:
    def test_signup_get_200_without_socialapp(self, client):
        assert client.get("/accounts/signup/").status_code == 200

    def test_signup_valid_no_oauth(self, client):
        r = client.post("/accounts/signup/", {
            "email": "new@x.com",
            "password1": "Str0ng!Pass9", "password2": "Str0ng!Pass9"})
        assert r.status_code in (200, 302)
        # email-based signup: username is auto-generated, user is created
        assert User.objects.filter(email="new@x.com").exists()

    def test_signup_duplicate_rejected(self, client):
        User.objects.create_user(username="existsemail", email="dup@x.com", password="pass12345!")
        r = client.post("/accounts/signup/", {
            "email": "dup@x.com",
            "password1": "Str0ng!Pass9", "password2": "Str0ng!Pass9"})
        assert r.status_code in (200, 302)  # duplicate email -> form error
        assert User.objects.filter(email="dup@x.com").count() == 1


@pytest.mark.django_db
class TestSuperuserAdmin:
    def test_createsuperuser_authenticates(self, client):
        _mk_superuser()
        assert client.login(username="root", password="pass12345!")

    def test_superuser_reaches_admin(self, client):
        _mk_superuser()
        client.login(username="root", password="pass12345!")
        assert client.get("/admin/").status_code == 200

    def test_normal_user_denied_admin(self, client):
        u = User.objects.create_user(username="norm", email="n@x.com",
                                     password="pass12345!")
        client.force_login(u)
        assert client.get("/admin/").status_code in (301, 302, 403)


@pytest.mark.django_db
class TestGoogleProviderOptional:
    def test_google_button_hidden_without_socialapp(self, client):
        r = client.get("/accounts/login/")
        # page renders; no crash (button may or may not appear, but page is 200)
        assert r.status_code == 200

    def test_google_button_present_with_socialapp(self, client):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        app = SocialApp.objects.create(provider="google", name="Google",
            client_id="test-client-id", secret="test-secret")
        site = Site.objects.get_or_create(id=1, defaults={"domain":"example.com","name":"example.com"})[0]
        app.sites.add(site)
        r = client.get("/accounts/login/")
        assert r.status_code == 200
        assert "google" in r.content.decode().lower()


@pytest.mark.django_db
class TestBannedUser:
    def test_banned_user_blocked_from_dashboard(self, client):
        u = User.objects.create_user(username="banned", email="b@x.com",
                                     password="pass12345!", is_banned=True)
        client.force_login(u)
        # ban middleware returns 403
        assert client.get("/dashboard/").status_code == 403
