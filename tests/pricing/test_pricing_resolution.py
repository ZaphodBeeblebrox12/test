"""Pricing architecture: deterministic country→region→base precedence,
correct geo/global persistence, immutable snapshot, single-active-base guard."""
import pytest
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from apps.subscriptions.models import GeoPlanPrice, Plan, PlanPrice, Subscription
from apps.subscriptions.services import resolve_plan_price, split_resolved_price

factory = RequestFactory()


def _req(country="US"):
    # resolver reads HTTP_CF_IPCOUNTRY (Cloudflare header) via request.META
    return factory.get("/", HTTP_CF_IPCOUNTRY=country)


def _mk_plan():
    return Plan.objects.create(name="Pro", display_order=1, tier="pro")


def _mk_base(plan, interval="monthly", cents=1000, currency="USD"):
    return PlanPrice.objects.create(plan=plan, interval=interval, price_cents=cents, currency=currency)


def _mk_geo(plan, country, cents, interval="monthly", region=None, currency="USD"):
    return GeoPlanPrice.objects.create(plan=plan, interval=interval, country=country,
                                       region=region, price_cents=cents, currency=currency)


@pytest.mark.django_db
class TestResolutionPrecedence:
    def test_country_beats_region_beats_base(self):
        plan = _mk_plan()
        base = _mk_base(plan, cents=1000)
        _mk_geo(plan, country=None, cents=2000, region="NA")  # regional (country=None)
        country_price = _mk_geo(plan, "US", 3000)             # country-specific
        r = resolve_plan_price(plan, "monthly", _req("US"))
        # country match returns the country row regardless of region
        assert r.pk == country_price.pk and r.price_cents == 3000

    def test_region_used_when_no_country(self):
        plan = _mk_plan(); base = _mk_base(plan)
        region_price = _mk_geo(plan, country=None, cents=2500, region="NA")  # regional via region field
        r = resolve_plan_price(plan, "monthly", _req("CA"))
        assert r.price_cents == 2500

    def test_base_used_when_no_geo(self):
        plan = _mk_plan(); base = _mk_base(plan, cents=1000)
        r = resolve_plan_price(plan, "monthly", _req("ZZ"))
        assert r.pk == base.pk and r.price_cents == 1000

    def test_missing_pricing_raises_deterministically(self):
        plan = _mk_plan()
        with pytest.raises(PlanPrice.DoesNotExist):
            resolve_plan_price(plan, "monthly", _req("ZZ"))


@pytest.mark.django_db
class TestSingleActiveBase:
    def test_second_active_base_rejected(self):
        plan = _mk_plan(); _mk_base(plan, currency="USD")
        with pytest.raises(ValidationError):
            _mk_base(plan, currency="EUR")  # clean()+save() raises

    def test_inactive_base_allowed(self):
        plan = _mk_plan(); _mk_base(plan, currency="USD")
        p2 = PlanPrice(plan=plan, interval="monthly", price_cents=900, currency="EUR", is_active=False)
        p2.save()  # inactive -> allowed
        assert PlanPrice.objects.filter(plan=plan).count() == 2


@pytest.mark.django_db
class TestPersistenceAndSnapshot:
    def test_geo_stored_in_geo_fk_not_plan_fk(self):
        plan = _mk_plan(); _mk_base(plan)
        geo = _mk_geo(plan, "US", 3000)
        fk = split_resolved_price(geo)
        assert fk["geo_plan_price"].pk == geo.pk and fk["plan_price"] is None
        assert fk["price_cents"] == 3000 and fk["price_currency"] == "USD"

    def test_global_stored_in_plan_fk(self):
        plan = _mk_plan(); base = _mk_base(plan)
        fk = split_resolved_price(base)
        assert fk["plan_price"].pk == base.pk and fk["geo_plan_price"] is None

    def test_snapshot_immutable(self):
        plan = _mk_plan(); base = _mk_base(plan, cents=1000)
        u = __import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model().objects.create(username="s")
        sub = Subscription.objects.create(user=u, plan=plan, plan_price=base,
                                          price_cents=1000, price_currency="USD")
        base2 = PlanPrice.objects.create(plan=plan, interval='yearly', price_cents=9999)  # later price change on a different interval
        _ = base2
        sub.refresh_from_db()
        assert sub.price_cents == 1000  # snapshot unchanged
        # direct edit of snapshot rejected by clean()
        sub.price_cents = 5000
        with pytest.raises(ValidationError):
            sub.full_clean()  # admin/forms path; save() intentionally skips to avoid blocking reads
