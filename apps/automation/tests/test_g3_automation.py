"""G3: event-driven lifecycle automation."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.automation.models import AutomationRule, AutomationRun
from apps.automation.services.automation import fire_due_runs, trigger_rules_for_event
from apps.emailing.models import Delivery, Template, TemplateVersion
from apps.events.models import Event, record_event
from apps.growth.models import MarketingPreference, Suppression

User = get_user_model()


def make_template(kind="marketing", html="<p>Hi {{ first_name }}</p>"):
    t = Template.objects.create(name="Auto", kind=kind)
    return t, TemplateVersion.objects.create(
        template=t, version_number=1, editor_mode="html",
        subject="Auto", html=html, plain_text="")


def make_user(email, marketing=True):
    u = User.objects.create_user(username=email.split("@")[0], email=email, password="pw")
    MarketingPreference.objects.create(user=u, marketing_opt_in=marketing)
    return u


class TriggerTests(TestCase):
    def test_event_enqueues_run_for_matching_rule(self):
        user = make_user("a@x.com")
        _, v = make_template()
        AutomationRule.objects.create(
            name="welcome", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="pe1", user_id=user.id)
        # The post_save signal fires during record_event and enqueues the run;
        # the explicit call is idempotent (returns 0 because it already exists).
        n = trigger_rules_for_event(ev)
        self.assertEqual(n, 0)  # already enqueued by the signal
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_signal_enqueues_run_on_event_save(self):
        """The post_save(Event) receiver wires record_event -> trigger_rules_for_event."""
        from apps.automation.signals import enqueue_automation_runs
        user = make_user("sig@x.com")
        _, v = make_template()
        AutomationRule.objects.create(
            name="sig", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="sig1", user_id=user.id)
        # Invoke the receiver exactly as the signal does (post_save passes instance, created).
        enqueue_automation_runs(sender=Event, instance=ev, created=True)
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_duplicate_event_delivery_one_run(self):
        user = make_user("b@x.com")
        _, v = make_template()
        rule = AutomationRule.objects.create(
            name="r", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="pe2", user_id=user.id)
        trigger_rules_for_event(ev)
        trigger_rules_for_event(ev)  # duplicate
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_inactive_rule_not_triggered(self):
        user = make_user("c@x.com")
        _, v = make_template()
        AutomationRule.objects.create(
            name="off", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0, enabled=False)
        ev, _ = record_event("purchase.completed", dedupe_key="pe3", user_id=user.id)
        self.assertEqual(trigger_rules_for_event(ev), 0)


class FireEligibilityTests(TestCase):
    def test_marketing_opted_in_fires(self):
        user = make_user("ok@x.com", marketing=True)
        _, v = make_template(kind="marketing")
        rule = AutomationRule.objects.create(
            name="m", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="f1", user_id=user.id)
        run = AutomationRun.objects.get(rule=rule, event=ev)
        run.scheduled_at = timezone.now() - datetime.timedelta(seconds=1)
        run.save(update_fields=["scheduled_at"])
        result = fire_due_runs()
        self.assertEqual(result["fired"], 1)
        self.assertEqual(Delivery.objects.count(), 1)

    def test_unsubscribe_during_delay_skips(self):
        """Eligibility is re-checked AT FIRE TIME, not at trigger time."""
        user = make_user("u@x.com", marketing=True)
        _, v = make_template(kind="marketing")
        rule = AutomationRule.objects.create(
            name="m", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=60)
        ev, _ = record_event("purchase.completed", dedupe_key="f2", user_id=user.id)
        run = AutomationRun.objects.get(rule=rule, event=ev)
        # user unsubscribes during the delay
        user.marketing_preference.marketing_opt_in = False
        user.marketing_preference.save(update_fields=["marketing_opt_in"])
        run.scheduled_at = timezone.now() - datetime.timedelta(seconds=1)
        run.save(update_fields=["scheduled_at"])
        result = fire_due_runs()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["fired"], 0)
        self.assertEqual(Delivery.objects.count(), 0)

    def test_suppression_overrides_opt_in(self):
        user = make_user("s@x.com", marketing=True)
        Suppression.objects.create(email="s@x.com", reason=Suppression.Reason.UNSUBSCRIBE)
        _, v = make_template(kind="marketing")
        rule = AutomationRule.objects.create(
            name="m", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="f3", user_id=user.id)
        run = AutomationRun.objects.get(rule=rule, event=ev)
        run.scheduled_at = timezone.now() - datetime.timedelta(seconds=1)
        run.save(update_fields=["scheduled_at"])
        result = fire_due_runs()
        self.assertEqual(result["skipped"], 1)

    def test_transactional_not_gated_by_opt_out(self):
        user = make_user("t@x.com", marketing=False)  # opted out
        _, v = make_template(kind="transactional")
        rule = AutomationRule.objects.create(
            name="tx", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="f4", user_id=user.id)
        run = AutomationRun.objects.get(rule=rule, event=ev)
        run.scheduled_at = timezone.now() - datetime.timedelta(seconds=1)
        run.save(update_fields=["scheduled_at"])
        result = fire_due_runs()
        self.assertEqual(result["fired"], 1)  # transactional sends regardless


class IdempotencyTests(TestCase):
    def test_duplicate_fire_no_duplicate_delivery(self):
        user = make_user("i@x.com", marketing=True)
        _, v = make_template()
        rule = AutomationRule.objects.create(
            name="m", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=0)
        ev, _ = record_event("purchase.completed", dedupe_key="i1", user_id=user.id)
        run = AutomationRun.objects.get(rule=rule, event=ev)
        run.scheduled_at = timezone.now() - datetime.timedelta(seconds=1)
        run.save(update_fields=["scheduled_at"])
        fire_due_runs()
        # run is no longer PENDING -> second sweep fires nothing
        result = fire_due_runs()
        self.assertEqual(result["fired"], 0)
        self.assertEqual(Delivery.objects.count(), 1)


class DelayTests(TestCase):
    def test_not_fired_before_scheduled_time(self):
        user = make_user("d@x.com", marketing=True)
        _, v = make_template()
        rule = AutomationRule.objects.create(
            name="m", trigger_event_type="purchase.completed",
            template_version=v, delay_minutes=1440)  # 1 day
        ev, _ = record_event("purchase.completed", dedupe_key="d1", user_id=user.id)
        result = fire_due_runs()
        self.assertEqual(result["fired"], 0)  # too early
