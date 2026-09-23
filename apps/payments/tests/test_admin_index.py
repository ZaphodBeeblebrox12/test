"""Grouped admin index: bucketing + health strip + render smoke."""
from django.contrib.auth import get_user_model
from django.template import Context
from django.test import TestCase

from apps.jobs.models import Job, PeriodicJob
from apps.payments.templatetags import payments_admin

User = get_user_model()


def _app(app_label, models):
    return {"app_label": app_label,
            "models": [{"object_name": m, "name": m, "app_label": app_label,
                        "admin_url": f"/admin/{app_label}/{m}/",
                        "add_url": ""} for m in models]}


class GroupedAppListTests(TestCase):
    def _groups(self, app_list):
        return payments_admin.grouped_app_list(Context({"app_list": app_list}))

    def test_known_models_bucketed(self):
        groups = self._groups([
            _app("payments", ["PaymentIntent"]),
            _app("subscriptions", ["Subscription", "Plan"]),
            _app("bot_integration", ["TelegramAccount"]),
        ])
        titles = {g["title"]: [m["object_name"] for m in g["models"]]
                  for g in groups}
        billing = next(v for k, v in titles.items() if "Billing" in k)
        access = next(v for k, v in titles.items() if "Access" in k)
        self.assertIn("PaymentIntent", billing)
        self.assertIn("Subscription", billing)
        self.assertIn("TelegramAccount", access)

    def test_unknown_models_land_in_other(self):
        groups = self._groups([_app("mystery", ["Widget"])])
        other = [g for g in groups if g["title"].endswith("Other")]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["models"][0]["object_name"], "Widget")

    def test_empty_app_list(self):
        self.assertEqual(self._groups([]), [])


class AdminHealthTests(TestCase):
    def test_health_reports_pending_and_periodic(self):
        Job.objects.create(kind="process_webhook_event", payload={},
                           status="pending")
        PeriodicJob.objects.create(name="channel_sync", interval_minutes=60,
                                   last_status="ok")
        h = payments_admin.admin_health()
        self.assertEqual(h["pending"], 1)
        self.assertEqual(h["periodic"][0]["name"], "channel_sync")


class AdminIndexRenderTests(TestCase):
    def test_admin_index_renders_existing_dashboard(self):
        # The project ships its own custom admin dashboard (marketing hub +
        # operational overview); this guards that it keeps rendering.
        admin = User.objects.create_superuser(username="root", email="r@r.r",
                                              password="x")
        self.client.force_login(admin)
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Operational overview", r.content.decode())
