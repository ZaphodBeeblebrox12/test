"""G4: coupons — eligibility, stacking, usage limits, idempotent redemption."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
import datetime

from apps.campaigns.models import Audience, Campaign
from apps.emailing.models import Template, TemplateVersion
from apps.payments.models import PaymentIntent
from apps.payments.services import activate_paid_subscription
from apps.promotions.models import Coupon, CouponRedemption
from apps.promotions.services.coupons import CouponError, apply_coupon, validate_coupon
from apps.subscriptions.models import Plan, Subscription

User = get_user_model()


def make_plan(tier="pro"):
    return Plan.objects.create(name="Pro", tier=tier, display_order=1)


def make_user(email):
    return User.objects.create_user(username=email.split("@")[0], email=email, password="pw")


def pct_coupon(**kw):
    d = dict(code="SAVE20", name="20% off", discount_type=Coupon.DiscountType.PERCENT,
             percent_off=20)
    d.update(kw)
    return Coupon.objects.create(**d)


class EligibilityTests(TestCase):
    def setUp(self):
        self.user = make_user("u@x.com")
        self.plan = make_plan()
        self.coupon = pct_coupon()

    def test_valid_percent(self):
        amt, coupon, discount = apply_coupon(
            user=self.user, plan=self.plan, base_amount_cents=1000, code="SAVE20")
        self.assertEqual(discount, 200)
        self.assertEqual(amt, 800)
        self.assertEqual(coupon.code, "SAVE20")

    def test_case_insensitive(self):
        amt, _, discount = apply_coupon(
            user=self.user, plan=self.plan, base_amount_cents=1000, code="save20")
        self.assertEqual(discount, 200)

    def test_invalid_code(self):
        with self.assertRaises(CouponError):
            apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="NOPE")

    def test_inactive(self):
        self.coupon.active = False
        self.coupon.save(update_fields=["active"])
        with self.assertRaises(CouponError) as e:
            validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=1000, has_referral_discount=False)
        self.assertIn("inactive", e.exception.reason)

    def test_expired(self):
        self.coupon.valid_until = timezone.now() - datetime.timedelta(days=1)
        self.coupon.save(update_fields=["valid_until"])
        with self.assertRaises(CouponError) as e:
            validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=1000, has_referral_discount=False)
        self.assertIn("expired", e.exception.reason)

    def test_not_started(self):
        self.coupon.valid_from = timezone.now() + datetime.timedelta(days=1)
        self.coupon.save(update_fields=["valid_from"])
        with self.assertRaises(CouponError):
            validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=1000, has_referral_discount=False)

    def test_plan_tier_restriction(self):
        self.coupon.plan_tiers = ["enterprise"]
        self.coupon.save(update_fields=["plan_tiers"])
        with self.assertRaises(CouponError) as e:
            validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=1000, has_referral_discount=False)
        self.assertIn("plan", e.exception.reason)

    def test_referral_stacking_blocked_by_default(self):
        with self.assertRaises(CouponError) as e:
            validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=1000, has_referral_discount=True)
        self.assertIn("referral", e.exception.reason)

    def test_referral_stacking_allowed_when_stackable(self):
        self.coupon.stackable_with_referral = True
        self.coupon.save(update_fields=["stackable_with_referral"])
        d = validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=800, has_referral_discount=True)
        self.assertEqual(d, 160)

    def test_fixed_amount(self):
        c = Coupon.objects.create(code="FLAT", name="Flat", discount_type=Coupon.DiscountType.FIXED,
                                  amount_off_cents=300)
        amt, _, discount = apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="FLAT")
        self.assertEqual(discount, 300)
        self.assertEqual(amt, 700)

    def test_discount_never_below_zero(self):
        c = Coupon.objects.create(code="BIG", name="Big", discount_type=Coupon.DiscountType.FIXED,
                                  amount_off_cents=9999)
        amt, _, discount = apply_coupon(user=self.user, plan=self.plan, base_amount_cents=500, code="BIG")
        self.assertEqual(discount, 500)
        self.assertEqual(amt, 0)


class UsageLimitTests(TestCase):
    def setUp(self):
        self.user = make_user("lim@x.com")
        self.plan = make_plan()
        self.coupon = pct_coupon(max_per_user=1, max_redemptions=2)

    def _intent(self, user, amount=1000):
        return PaymentIntent.objects.create(
            user=user, plan=self.plan, amount=amount, currency="USD",
            status="pending", provider="stripe", provider_reference=f"cs-{user.username}")

    def test_per_user_limit(self):
        CouponRedemption.objects.create(
            coupon=self.coupon, user=self.user, payment_intent=self._intent(self.user))
        with self.assertRaises(CouponError) as e:
            validate_coupon(self.coupon, user=self.user, plan=self.plan,
                            base_amount_cents=1000, has_referral_discount=False)
        self.assertIn("limit", e.exception.reason)

    def test_global_limit_no_over_redeem(self):
        u2 = make_user("g2@x.com")
        u3 = make_user("g3@x.com")
        from apps.promotions.services.coupons import redeem_coupon
        self.assertTrue(redeem_coupon(coupon=self.coupon, user=self.user,
                                      payment_intent=self._intent(self.user),
                                      subscription=None, discount_cents=200))
        self.assertTrue(redeem_coupon(coupon=self.coupon, user=u2,
                                      payment_intent=self._intent(u2),
                                      subscription=None, discount_cents=200))
        # third concurrent redemption exceeds max_redemptions=2
        self.assertFalse(redeem_coupon(coupon=self.coupon, user=u3,
                                       payment_intent=self._intent(u3),
                                       subscription=None, discount_cents=200))

    def test_duplicate_redemption_idempotent(self):
        from apps.promotions.services.coupons import redeem_coupon
        intent = self._intent(self.user)
        self.assertTrue(redeem_coupon(coupon=self.coupon, user=self.user,
                                      payment_intent=intent, subscription=None, discount_cents=200))
        # same payment_intent again -> get_or_create returns existing (False)
        self.assertFalse(redeem_coupon(coupon=self.coupon, user=self.user,
                                       payment_intent=intent, subscription=None, discount_cents=200))
        self.assertEqual(CouponRedemption.objects.count(), 1)


class PaymentIntegrationTests(TestCase):
    def setUp(self):
        self.user = make_user("pay@x.com")
        self.plan = make_plan()

    def test_activation_redeems_and_snapshots(self):
        from apps.promotions.services.coupons import redeem_coupon
        coupon = pct_coupon(code="PAY20")
        intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, base_amount_cents=1000, amount=800,
            currency="USD", status="pending", provider="stripe",
            provider_reference="cs-pay", applied_coupon_code="PAY20", coupon_discount_cents=200)
        activated, sub = activate_paid_subscription(intent)
        self.assertTrue(activated)
        # redemption finalized with provenance
        red = CouponRedemption.objects.get()
        self.assertTrue(red.finalized)
        self.assertEqual(red.base_amount_cents, 1000)
        self.assertEqual(red.discount_cents, 200)
        self.assertEqual(red.final_amount_cents, 800)
        self.assertEqual(red.subscription_id, sub.id)
        # subscription snapshot provenance
        self.assertEqual(sub.price_cents, 800)          # final
        self.assertEqual(sub.base_price_cents, 1000)    # base
        self.assertEqual(sub.discount_cents, 200)
        self.assertEqual(sub.coupon_code, "PAY20")

    def test_duplicate_activation_single_redemption(self):
        coupon = pct_coupon(code="DUP")
        intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, base_amount_cents=1000, amount=800,
            currency="USD", status="pending", provider="stripe",
            provider_reference="cs-dup", applied_coupon_code="DUP", coupon_discount_cents=200)
        activate_paid_subscription(intent)
        # second call on the already-SUCCESS intent -> no new redemption
        activate_paid_subscription(intent)
        self.assertEqual(CouponRedemption.objects.count(), 1)
def make_template(name="T", kind="marketing"):
    t = Template.objects.create(name=name, kind=kind)
    return TemplateVersion.objects.create(template=t, version_number=1, editor_mode="html",
                                          subject="s", html="<p>x</p>")


class CampaignScopedCouponTests(TestCase):
    """Model C: OPTIONAL campaign scoping.  campaign=NULL = unscoped (existing
    behavior); campaign=X = only users whose SignupAttribution.referring_campaign
    is X may redeem.  SET_NULL on campaign delete -> unscoped."""

    def setUp(self):
        self.user = make_user("sc@x.com")
        self.other_user = make_user("oc@x.com")
        self.plan = make_plan()
        self.aud = Audience.objects.create(name="A", rules={})
        self.tv = make_template()
        self.campaign_a = Campaign.objects.create(name="CampA", template_version=self.tv, audience=self.aud)
        self.campaign_b = Campaign.objects.create(name="CampB", template_version=self.tv, audience=self.aud)

    def _scoped(self, campaign, code="SCOPED"):
        return Coupon.objects.create(code=code, name="Scoped", discount_type=Coupon.DiscountType.PERCENT,
                                     percent_off=20, campaign=campaign)

    def _attr(self, user, campaign):
        from apps.analytics.models import SignupAttribution
        SignupAttribution.objects.create(user=user, referring_campaign=campaign)

    def test_a_unscoped_coupon_still_works(self):
        Coupon.objects.create(code="OPEN", name="Open", discount_type=Coupon.DiscountType.PERCENT, percent_off=20)
        amt, coupon, discount = apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="OPEN")
        self.assertEqual(discount, 200)
        self.assertIsNone(coupon.campaign_id)

    def test_b_scoped_works_for_eligible_user(self):
        c = self._scoped(self.campaign_a)
        self._attr(self.user, self.campaign_a)
        amt, _, discount = apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="SCOPED")
        self.assertEqual(discount, 200)

    def test_c_rejected_for_different_campaign_user(self):
        c = self._scoped(self.campaign_a)
        self._attr(self.user, self.campaign_b)  # attributed to B, coupon is A
        with self.assertRaises(CouponError):
            apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="SCOPED")

    def test_d_rejected_for_no_attribution_user(self):
        self._scoped(self.campaign_a)
        with self.assertRaises(CouponError) as e:
            apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="SCOPED")
        self.assertIn("eligible", e.exception.reason)  # generic, no campaign leak

    def test_e_cannot_forge_with_client_campaign_id(self):
        """validate_coupon uses the Coupon's own campaign FK, never a client id."""
        c = self._scoped(self.campaign_a)
        self._attr(self.user, self.campaign_b)
        # Even if the client somehow passes a campaign id, eligibility is the Coupon's FK.
        with self.assertRaises(CouponError):
            validate_coupon(c, user=self.user, plan=self.plan, base_amount_cents=1000,
                            has_referral_discount=False)

    def test_f_campaign_deleted_becomes_unscoped(self):
        c = self._scoped(self.campaign_a)
        self.campaign_a.delete()  # SET_NULL
        c.refresh_from_db()
        self.assertIsNone(c.campaign_id)  # unscoped -> works for anyone
        amt, _, discount = apply_coupon(user=self.other_user, plan=self.plan, base_amount_cents=1000, code="SCOPED")
        self.assertEqual(discount, 200)

    def test_g_existing_rules_still_apply_when_scoped(self):
        c = self._scoped(self.campaign_a)
        c.active = False
        c.save(update_fields=["active"])
        self._attr(self.user, self.campaign_a)
        with self.assertRaises(CouponError):  # inactive still rejected
            apply_coupon(user=self.user, plan=self.plan, base_amount_cents=1000, code="SCOPED")

    def test_h_snapshot_immutable_after_scoped_apply(self):
        from apps.payments.services import activate_paid_subscription
        c = self._scoped(self.campaign_a, code="SNAP")
        self._attr(self.user, self.campaign_a)
        intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, base_amount_cents=1000, amount=800,
            currency="USD", status="pending", provider="stripe",
            provider_reference="cs-snap", applied_coupon_code="SNAP", coupon_discount_cents=200)
        activated, sub = activate_paid_subscription(intent)
        self.assertEqual(sub.price_cents, 800)
        self.assertEqual(sub.base_price_cents, 1000)
        self.assertEqual(sub.discount_cents, 200)
        # campaign deleted AFTER purchase -> snapshot unchanged
        self.campaign_a.delete()
        sub.refresh_from_db()
        self.assertEqual(sub.price_cents, 800)

    def test_i_confirm_does_not_recheck_campaign(self):
        # confirmation path = activate_paid_subscription, which never calls validate_coupon
        import inspect
        from apps.payments import services as pay_svc
        self.assertNotIn("validate_coupon", inspect.getsource(pay_svc.activate_paid_subscription))

    def test_j_renewal_does_not_reapply_coupon(self):
        from apps.subscriptions.services import extend_subscription
        c = self._scoped(self.campaign_a)
        self._attr(self.user, self.campaign_a)
        intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, base_amount_cents=1000, amount=800,
            currency="USD", status="pending", provider="stripe",
            provider_reference="cs-renew", applied_coupon_code="SCOPED", coupon_discount_cents=200)
        activated, sub = activate_paid_subscription(intent)
        # renew + delete campaign; subscription price must not change
        self.campaign_a.delete()
        extend_subscription(sub, days=30, actor="webhook")
        sub.refresh_from_db()
        self.assertEqual(sub.price_cents, 800)
        self.assertEqual(sub.discount_cents, 200)  # initial discount, not reapplied
