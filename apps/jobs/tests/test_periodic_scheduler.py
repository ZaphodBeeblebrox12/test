"""PeriodicJob + trickle window scheduler tests."""
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.bot_integration.models import CommunityChannel, TelegramAccount
from apps.bot_integration.services import channel_sync
from apps.jobs import scheduler
from apps.jobs.models import PeriodicJob

User = get_user_model()


def _job(**kw):
    """Fetch the migration-seeded row (unique name) and reset it for the test."""
    obj = PeriodicJob.objects.get(name="sync_channel_memberships")
    obj.enabled = kw.get("enabled", True)
    obj.interval_minutes = kw.get("interval_minutes", 1)
    obj.max_calls_per_run = kw.get("max_calls_per_run", 10)
    obj.state = kw.get("state", {})
    obj.last_run_at = kw.get("last_run_at")
    obj.last_status = ""
    obj.last_error = ""
    obj.save()
    return obj


class PeriodicJobTests(TestCase):
    def test_due_when_never_run(self):
        self.assertTrue(_job(last_run_at=None).is_due())

    def test_not_due_within_interval(self):
        self.assertFalse(_job(last_run_at=timezone.now()).is_due())

    def test_due_after_interval(self):
        self.assertTrue(_job(last_run_at=timezone.now()
                            - timezone.timedelta(minutes=2)).is_due())

    def test_disabled_never_due(self):
        self.assertFalse(_job(enabled=False).is_due())

    def test_seed_row_created_by_migration(self):
        self.assertTrue(PeriodicJob.objects.filter(
            name="sync_channel_memberships").exists())


class SchedulerRunDueTests(TestCase):
    def setUp(self):
        # Isolate from other migration-seeded jobs (winback/welcome).
        PeriodicJob.objects.exclude(name="sync_channel_memberships").delete()
        self.job = _job(last_run_at=None)

    def test_due_job_executes_with_job_arg_and_marks_ok(self):
        with mock.patch(
                "apps.bot_integration.services.channel_sync."
                "sync_channel_memberships_window", return_value=10) as fn:
            ran = scheduler._run_due()
        self.assertEqual(ran, 1)
        fn.assert_called_once()
        self.assertEqual(fn.call_args[0][0].pk, self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.last_status, PeriodicJob.Status.OK)
        self.assertIsNotNone(self.job.last_run_at)

    def test_handler_error_marked_and_swallowed(self):
        with mock.patch(
                "apps.bot_integration.services.channel_sync."
                "sync_channel_memberships_window",
                side_effect=RuntimeError("boom")):
            ran = scheduler._run_due()
        self.assertEqual(ran, 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.last_status, PeriodicJob.Status.ERROR)
        self.assertIn("boom", self.job.last_error)

    def test_not_due_job_skipped(self):
        _job(last_run_at=timezone.now())
        with mock.patch(
                "apps.bot_integration.services.channel_sync."
                "sync_channel_memberships_window") as fn:
            ran = scheduler._run_due()
        self.assertEqual(ran, 0)
        fn.assert_not_called()

    def test_unknown_handler_marked_error(self):
        bad = PeriodicJob.objects.create(name="no_such_job", interval_minutes=1)
        ran = scheduler._run_due()
        # Only the registered job counts as executed; the unknown one is
        # stamped ERROR but never runs.
        self.assertEqual(ran, 1)
        bad.refresh_from_db()
        self.assertEqual(bad.last_status, PeriodicJob.Status.ERROR)


class TrickleWindowTests(TestCase):
    def setUp(self):
        self.ch = CommunityChannel.objects.create(
            platform="telegram", external_id="@free", name="Free")
        # Fixed UUIDs so user_id ordering == creation order (deterministic).
        self.users = [User.objects.create_user(
            username=f"w{i}", password="x", id=uuid.UUID(int=100 + i))
            for i in range(5)]
        self.accounts = [TelegramAccount.objects.create(user=u, chat_id=100 + i)
                         for i, u in enumerate(self.users)]
        self.job = _job(max_calls_per_run=3, state={},
                        last_run_at=None)  # 3 calls = 1 user (1 channel)

    def _member(self, *a, **k):
        return {"ok": True, "result": {"status": "member"}}

    def test_window_respects_budget_and_advances_cursor(self):
        # budget=3 calls, 1 channel -> exactly 3 users per window.
        with mock.patch.object(channel_sync.TelegramBotService, "_api_request",
                               side_effect=self._member) as api:
            calls = channel_sync.sync_channel_memberships_window(self.job)
        self.assertEqual(calls, 3)
        self.assertEqual(api.call_count, 3)  # never exceeded the budget
        self.job.refresh_from_db()
        self.assertEqual(self.job.state["after_user_id"],
                         str(self.accounts[2].user_id))

    def test_next_window_resumes_after_cursor(self):
        with mock.patch.object(channel_sync.TelegramBotService, "_api_request",
                               side_effect=self._member):
            first = channel_sync.sync_channel_memberships_window(self.job)
            second = channel_sync.sync_channel_memberships_window(self.job)
        self.assertEqual(first, 3)   # users 1-3
        self.assertEqual(second, 2)  # users 4-5 -> pass complete
        self.job.refresh_from_db()
        self.assertIsNone(self.job.state["after_user_id"])  # wrapped

    def test_wraps_to_start_after_full_pass(self):
        self.job.max_calls_per_run = 100
        self.job.save()
        with mock.patch.object(channel_sync.TelegramBotService, "_api_request",
                               side_effect=self._member):
            calls = channel_sync.sync_channel_memberships_window(self.job)
            nxt = channel_sync.sync_channel_memberships_window(self.job)
        self.assertEqual(calls, 5)
        self.assertEqual(nxt, 5)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.state["after_user_id"])

    def test_no_channels_consumes_nothing(self):
        CommunityChannel.objects.all().delete()
        calls = channel_sync.sync_channel_memberships_window(self.job)
        self.assertEqual(calls, 0)


class QueueDrainTests(TestCase):
    def test_drain_processes_until_empty(self):
        from types import SimpleNamespace
        jobs = [SimpleNamespace(pk=i) for i in range(3)]
        with mock.patch("apps.jobs.worker.claim_next",
                        side_effect=jobs + [None]) as claim, \
             mock.patch("apps.jobs.worker.process_job") as proc, \
             mock.patch("apps.jobs.worker.reaper_reset"), \
             mock.patch("apps.jobs.handlers.register_builtin_handlers"):
            processed = scheduler._drain_durable_queue(max_jobs=10)
        self.assertEqual(processed, 3)
        self.assertEqual(proc.call_count, 3)

    def test_drain_respects_max_jobs(self):
        from types import SimpleNamespace
        with mock.patch("apps.jobs.worker.claim_next",
                        side_effect=[SimpleNamespace(pk=1)] * 10), \
             mock.patch("apps.jobs.worker.process_job"), \
             mock.patch("apps.jobs.worker.reaper_reset"), \
             mock.patch("apps.jobs.handlers.register_builtin_handlers"):
            processed = scheduler._drain_durable_queue(max_jobs=2)
        self.assertEqual(processed, 2)

    def test_drain_swallows_errors(self):
        with mock.patch("apps.jobs.worker.claim_next",
                        side_effect=RuntimeError("db down")):
            self.assertEqual(scheduler._drain_durable_queue(), 0)
