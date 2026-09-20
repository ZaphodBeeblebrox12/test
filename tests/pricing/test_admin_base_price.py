"""Regression: Global base + geo overrides coexist; duplicate global base is a
normal inline error (not 500)."""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


def _admin(client):
    u = User.objects.create_superuser(username="adm", email="a@x.com", password="pass12345!")
    client.force_login(u)
    return u


PLAN = {"name": "Pro", "tier": "pro", "display_order": "1",
        "max_projects": "5", "max_storage_mb": "100", "api_calls_per_day": "1000"}


@pytest.mark.django_db
def test_global_plus_geo_overrides_save(client):
    """Global Monthly USD + Geo US USD + Geo IN INR MUST save (valid)."""
    _admin(client)
    data = {
        **PLAN,
        "prices-TOTAL_FORMS": "1", "prices-INITIAL_FORMS": "0",
        "prices-MIN_NUM_FORMS": "0", "prices-MAX_NUM_FORMS": "1000",
        "prices-0-id": "", "prices-0-interval": "monthly",
        "prices-0-price_cents": "1000", "prices-0-currency": "USD", "prices-0-is_active": "on",
        "geo_prices-TOTAL_FORMS": "2", "geo_prices-INITIAL_FORMS": "0",
        "geo_prices-MIN_NUM_FORMS": "0", "geo_prices-MAX_NUM_FORMS": "1000",
        "geo_prices-0-id": "", "geo_prices-0-interval": "monthly",
        "geo_prices-0-price_cents": "2000", "geo_prices-0-currency": "USD",
        "geo_prices-0-country": "US", "geo_prices-0-region": "NA", "geo_prices-0-is_active": "on",
        "geo_prices-1-id": "", "geo_prices-1-interval": "monthly",
        "geo_prices-1-price_cents": "800", "geo_prices-1-currency": "INR",
        "geo_prices-1-country": "IN", "geo_prices-1-region": "APAC", "geo_prices-1-is_active": "on",
    }
    r = client.post(reverse("admin:subscriptions_plan_add"), data, follow=True)
    from apps.subscriptions.models import Plan, PlanPrice, GeoPlanPrice
    assert Plan.objects.filter(name="Pro").exists(), "valid global+geo config must save"
    assert PlanPrice.objects.filter(plan__name="Pro").count() == 1
    assert GeoPlanPrice.objects.filter(plan__name="Pro").count() == 2


@pytest.mark.django_db
def test_duplicate_global_base_is_inline_error_not_500(client):
    """Global Monthly USD + Global Monthly INR must be a normal inline error."""
    _admin(client)
    data = {
        **PLAN,
        "prices-TOTAL_FORMS": "2", "prices-INITIAL_FORMS": "0",
        "prices-MIN_NUM_FORMS": "0", "prices-MAX_NUM_FORMS": "1000",
        "prices-0-id": "", "prices-1-id": "",
        "prices-0-interval": "monthly", "prices-0-price_cents": "1000",
        "prices-0-currency": "USD", "prices-0-is_active": "on",
        "prices-1-interval": "monthly", "prices-1-price_cents": "80000",
        "prices-1-currency": "INR", "prices-1-is_active": "on",
        "geo_prices-TOTAL_FORMS": "0", "geo_prices-INITIAL_FORMS": "0",
        "geo_prices-MIN_NUM_FORMS": "0", "geo_prices-MAX_NUM_FORMS": "1000",
    }
    r = client.post(reverse("admin:subscriptions_plan_add"), data)
    assert r.status_code == 200, f"duplicate global base must not 500 (got {r.status_code})"
    assert "one active global base price" in r.content.decode().lower()
