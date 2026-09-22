"""Rendering validation for the branded email templates.

Critical because NotificationService SILENTLY drops the HTML alternative when
a template fails to render (try/except around the html get_template) — these
tests are the guard that the branded emails actually go out as HTML.
"""
from django.core import mail
from django.template.loader import render_to_string
from django.test import TestCase

from apps.notifications.services import NotificationService

BASE_CTX = {
    "site_name": "TradeAdmin",
    "username": "alice",
    "plan_name": "Pro",
    "amount_display": "USD 9.99",
    "amount": "USD 9.99",
    "currency": "USD",
    "provider_display": "Stripe",
    "provider_reference": "cs_test_1",
    "provider_payment_id": "pi_1",
    "date_display": "Sep 22, 2026",
    "dashboard_url": "https://example.com/dashboard/",
    "support_url": "https://example.com/support/",
    "refunded_amount": "USD 5.00",
    "fully_refunded": False,
    "expires_display": "Oct 22, 2026",
}


class BillingEmailTemplateTests(TestCase):
    def test_all_billing_templates_render(self):
        for name in ("payment_success", "payment_failed",
                     "payment_refunded", "payment_chargedback"):
            html = render_to_string(f"payments/email/{name}_body.html", BASE_CTX)
            self.assertIn("TradeAdmin", html)          # branding
            self.assertIn("USD 9.99", html)            # payment data present
            self.assertIn("alice", html)               # personalization

    def test_billing_templates_have_cta_button(self):
        html = render_to_string("payments/email/payment_success_body.html", BASE_CTX)
        self.assertIn("View subscription", html)
        self.assertIn("https://example.com/dashboard/", html)

    def test_reminder_templates_render(self):
        ctx = dict(BASE_CTX, days=3)
        for name in ("reminder_pre_expiry", "reminder_post_expiry"):
            html = render_to_string(f"subscriptions/email/{name}_body.html", ctx)
            self.assertIn("TradeAdmin", html)

    def test_notification_service_attaches_html(self):
        NotificationService.send_email(
            "a@example.com", "payments/email/payment_success",
            "Receipt", dict(BASE_CTX))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertTrue(any("Payment successful" in alt[0]
                            for alt in msg.alternatives),
                        "HTML alternative missing — template silently dropped")

    def test_notification_service_html_for_chargeback(self):
        NotificationService.send_email(
            "a@example.com", "payments/email/payment_chargedback",
            "Disputed", dict(BASE_CTX))
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(any("subscription cancelled" in alt[0].lower()
                            for alt in msg_alternatives(mail.outbox[0])))


def msg_alternatives(message):
    return getattr(message, "alternatives", [])
