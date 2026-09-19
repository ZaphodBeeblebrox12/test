"""Regression: /accounts/login/ must not 500 when an optional social provider
has no SocialApp configured (allauth get_providers is empty -> block skipped).

Root cause: login.html hard-rendered {% provider_login_url 'google' %}, which
raises SocialApp.DoesNotExist when unconfigured, crashing the whole page.
Fix: wrap the block in {% get_providers %} ... {% if 'google' in ... %}.
"""
import pytest
from django.test import TestCase


class LoginPageSocialProviderTest(TestCase):
    def test_login_page_200_without_socialapp(self):
        """No SocialApp configured -> page must still render (no 500)."""
        r = self.client.get("/accounts/login/")
        assert r.status_code == 200

    def test_login_page_200_with_google_socialapp(self):
        """A configured SocialApp -> page renders and Google button present."""
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        app = SocialApp.objects.create(
            provider="google", name="Google",
            client_id="test-google-client-id", secret="test-google-secret")
        site = Site.objects.get_or_create(
            id=1, defaults={"domain": "example.com", "name": "example.com"})[0]
        app.sites.add(site)
        r = self.client.get("/accounts/login/")
        assert r.status_code == 200
        assert "google" in r.content.decode().lower()
