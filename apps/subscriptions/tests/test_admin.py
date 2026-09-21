"""P3a: Admin safety — read-only lifecycle/financial fields, validation, registration."""
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from apps.bot_integration.admin import UserChannelAssignmentAdmin
from apps.bot_integration.models import (
    BotAccessAudit, TelegramVerificationToken, UserChannelAssignment)
from apps.jobs.admin import JobAdmin
from apps.jobs.models import Job
from apps.payments.admin import PaymentIntentAdmin
from apps.payments.models import PaymentIntent
from apps.subscriptions.admin import (
    GeoPlanPriceInline, PlanAdmin, PlanPriceInline, SubscriptionAdmin)
from apps.subscriptions.models import (
    GeoPlanPrice, Plan, PlanPrice, Subscription, SubscriptionHistory,
    UpgradeHistory)

User = get_user_model()
rf = RequestFactory()


def make_admin_user():
    import uuid
    u = User(username=f"admin_{uuid.uuid4().hex[:8]}")
    u.is_staff = True
    u.is_superuser = True
    u.set_password("pw")
    u.save()
    return u


def authed_req():
    req = rf.get("/admin/")
    req.user = make_admin_user()
    return req


def admin_site():
    return admin.site


class GeoPlanPriceValidationTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(name="Pro", tier="pro", display_order=1)

    def _gp(self, country=None, region=None):
        return GeoPlanPrice(
            plan=self.plan, country=country, region=region,
            price_cents=999, currency="USD", interval="monthly")

    def test_country_only_valid(self):
        self._gp(country="US").full_clean(exclude=["id"])  # should not raise

    def test_region_only_valid(self):
        self._gp(region="EU").full_clean(exclude=["id"])

    def test_both_country_and_region_valid(self):
        # A GeoPlanPrice carrying BOTH country and region is valid here:
        # resolve_plan_price checks country match first, then region, so the
        # pair is unambiguous (tests/pricing/test_admin_base_price.py
        # intentionally saves country+region rows and asserts they persist).
        self._gp(country="US", region="NA").full_clean(exclude=["id"])

    def test_neither_invalid(self):
        with self.assertRaises(ValidationError):
            self._gp().full_clean(exclude=["id"])

    def test_full_clean_raises_outside_admin(self):
        # Validation lives in clean()/full_clean() (what the admin form and
        # any .full_clean() caller use); a GeoPlanPrice with NEITHER country
        # nor region must be rejected even outside the admin.
        gp = self._gp()
        with self.assertRaises(ValidationError):
            gp.full_clean(exclude=["id"])


class PlanAdminInlineTests(TestCase):
    def test_planprice_inline_registered(self):
        inlines = [i.model for i in PlanAdmin(Plan, admin_site()).get_inline_instances(authed_req())]
        self.assertIn(PlanPrice, inlines)

    def test_geoplanprice_inline_registered(self):
        inlines = [i.model for i in PlanAdmin(Plan, admin_site()).get_inline_instances(authed_req())]
        self.assertIn(GeoPlanPrice, inlines)


class SubscriptionReadOnlyTests(TestCase):
    def test_lifecycle_fields_readonly(self):
        ma = SubscriptionAdmin(Subscription, admin_site())
        ro = ma.get_readonly_fields(authed_req(), obj=None)
        for f in ("status", "is_active", "expires_at", "price_cents",
                  "payment_provider", "user", "plan", "provider_subscription_id",
                  "pricing_country"):
            self.assertIn(f, ro)

    def test_cannot_delete_subscription(self):
        ma = SubscriptionAdmin(Subscription, admin_site())
        self.assertFalse(ma.has_delete_permission(authed_req()))


class PaymentReadOnlyTests(TestCase):
    def test_all_fields_readonly(self):
        ma = PaymentIntentAdmin(PaymentIntent, admin_site())
        ro = ma.get_readonly_fields(authed_req(), obj=None)
        field_names = [f.name for f in PaymentIntent._meta.fields]
        self.assertTrue(set(field_names).issubset(set(ro)))
        self.assertIn("status", ro)
        self.assertIn("amount", ro)

    def test_cannot_delete_payment_intent(self):
        ma = PaymentIntentAdmin(PaymentIntent, admin_site())
        self.assertFalse(ma.has_delete_permission(authed_req()))


class AuditReadOnlyRegistrationTests(TestCase):
    def test_history_readonly_permissions(self):
        ma = admin.site._registry[SubscriptionHistory]
        self.assertFalse(ma.has_change_permission(authed_req()))

    def test_upgrade_history_readonly(self):
        ma = admin.site._registry[UpgradeHistory]
        self.assertFalse(ma.has_change_permission(authed_req()))

    def test_access_audit_readonly(self):
        ma = admin.site._registry[BotAccessAudit]
        self.assertFalse(ma.has_change_permission(authed_req()))

    def test_verification_token_readonly(self):
        ma = admin.site._registry[TelegramVerificationToken]
        self.assertFalse(ma.has_change_permission(authed_req()))


class UserChannelAssignmentProtectionTests(TestCase):
    def test_lifecycle_fields_readonly(self):
        ma = UserChannelAssignmentAdmin(UserChannelAssignment, admin_site())
        ro = ma.get_readonly_fields(authed_req(), obj=None)
        for f in ("is_active", "last_invite_sent_at", "revoked_at"):
            self.assertIn(f, ro)


class JobAdminTests(TestCase):
    def test_job_registered(self):
        self.assertIn(Job, admin.site._registry)

    def test_job_readonly_and_no_add_delete(self):
        ma = admin.site._registry[Job]
        ro = ma.get_readonly_fields(authed_req(), obj=None)
        self.assertIn("status", ro)
        self.assertIn("kind", ro)
        self.assertFalse(ma.has_add_permission(authed_req()))
        self.assertFalse(ma.has_delete_permission(authed_req()))


class ReferralAdminRegistrationTests(TestCase):
    def test_referral_models_registered(self):
        from apps.growth import models as gm
        for name in ("ReferralCode", "ReferralSettings"):
            model = getattr(gm, name, None)
            if model is not None:
                self.assertIn(model, admin.site._registry)
