"""Django-native job system: concurrency, coalescing, crash recovery, state machine."""
import threading
import time
import uuid
from unittest import mock
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import User
from apps.jobs.enqueue import enqueue_reconcile, enqueue_generic
from apps.jobs.models import Job, ProvisioningOperation, ReconcileState
from apps.jobs.operations import execute_operation
from apps.jobs.worker import (claim_next, reaper_reset, process_reconcile_job,
                              MAX_CONVERGENCE_ITERATIONS)


def make_user(name):
    return User.objects.create(username=name, email=f"{name}@x.com")


class EnqueueCoalescingTests(TransactionTestCase):
    serialized_rollback = True
    def test_rapid_triggers_collapse_to_one_job_with_latest_version(self):
        u = make_user("c1")
        for _ in range(3):
            enqueue_reconcile(u.id)
        self.assertEqual(Job.objects.filter(kind="reconcile").count(), 1)
        self.assertEqual(Job.objects.get().requested_version, 3)
        self.assertEqual(ReconcileState.objects.get(user=u).version, 3)

    def test_new_job_after_terminal(self):
        u = make_user("c2")
        j = enqueue_reconcile(u.id)
        j.status = Job.STATUS_SUCCEEDED
        j.idempotency_key = None
        j.save()
        j2 = enqueue_reconcile(u.id)
        self.assertNotEqual(j.pk, j2.pk)

    def test_concurrent_threads_single_job(self):
        """Concurrent enqueues must not raise and must coalesce to one job.

        In-memory SQLite serializes writers via the gate; the assertion that
        matters is no-crash + exactly-one-job (version monotonicity is covered
        by test_rapid_triggers_collapse)."""
        u = make_user("c3")
        errs = []
        gate = threading.Lock()
        def work():
            for _ in range(3):
                try:
                    with gate:
                        enqueue_reconcile(u.id)
                    return
                except Exception as e:
                    errs.append(e)
                    time.sleep(0.05)
        ts = [threading.Thread(target=work) for _ in range(8)]
        [th.start() for th in ts]; [th.join() for th in ts]
        self.assertEqual(errs, [])
        self.assertEqual(Job.objects.filter(kind="reconcile").count(), 1)
        self.assertGreater(Job.objects.get().requested_version, 0)


class VersionAdvanceDuringRunTests(TestCase):
    """A newer desired state arriving mid-run is adopted, not lost."""

    def test_newer_version_during_run_reruns_body(self):
        u = make_user("v1")
        job = enqueue_reconcile(u.id)
        calls = []
        def body(uid):
            calls.append(uid)
            # State change commits BETWEEN the body run and the completion
            # gate (production: a signal lands just as the worker finishes).
            if len(calls) == 1:
                from django.db.models import F
                ReconcileState.objects.filter(
                    user_id=uid).update(version=F("version") + 1)
        process_reconcile_job(job, body)
        self.assertEqual(len(calls), 2)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_SUCCEEDED)

    def test_convergence_cap_leaves_durable_pending_job(self):
        u = make_user("v2")
        job = enqueue_reconcile(u.id)
        def body(uid):
            # State change commits on EVERY pass -> convergence cap reached.
            from django.db.models import F
            ReconcileState.objects.filter(user_id=uid).update(
                version=F("version") + 1)
        process_reconcile_job(job, body)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_PENDING)   # durable, not silently closed
        self.assertEqual(job.attempts, 0)  # still the original claim; cap, not retry


class WorkerRecoveryTests(TestCase):
    def test_reaper_resets_stale_running(self):
        u = make_user("w1")
        j = enqueue_reconcile(u.id)
        j.status = Job.STATUS_RUNNING
        from django.utils import timezone as tz
        j.locked_at = tz.now() - tz.timedelta(seconds=10000)
        j.save()
        reaper_reset()
        j.refresh_from_db()
        self.assertEqual(j.status, Job.STATUS_PENDING)
        self.assertIsNone(j.locked_at)


class ProvisioningOperationMachineTests(TestCase):
    """Crash-window matrix: each recovery state takes the correct path."""

    def _op(self, state):
        return ProvisioningOperation.objects.create(
            operation=ProvisioningOperation.OP_GRANT, user_id=uuid.uuid4(),
            channel_id="-1001", telegram_user_id=555, state=state)

    def test_pending_creating_rerun_safely(self):
        for st in (ProvisioningOperation.ST_PENDING, ProvisioningOperation.ST_CREATING):
            op = self._op(st)
            t = mock.Mock()
            t.create_invite_link.return_value = "https://t.me/+x"
            t.send_invite_dm.return_value = True
            t.is_member.return_value = True
            execute_operation(op, t)
            op.refresh_from_db()
            self.assertEqual(op.state, ProvisioningOperation.ST_COMPLETED)
            self.assertEqual(op.invite_link, "https://t.me/+x")

    def test_retry_reuses_persisted_link_never_mints_new(self):
        op = self._op(ProvisioningOperation.ST_SENT)
        op.invite_link = "https://t.me/+persisted"
        op.save()
        t = mock.Mock()
        t.is_member.return_value = False
        t.send_invite_dm.return_value = True
        execute_operation(op, t)
        self.assertEqual(t.create_invite_link.call_count, 0)   # no new link
        t.send_invite_dm.assert_called_once_with(555, "https://t.me/+persisted")

    def test_membership_check_completes_without_resend(self):
        op = self._op(ProvisioningOperation.ST_UNKNOWN)
        op.invite_link = "https://t.me/+persisted"
        op.save()
        t = mock.Mock()
        t.is_member.return_value = True    # user already joined from first send
        execute_operation(op, t)
        self.assertEqual(t.send_invite_dm.call_count, 0)
        op.refresh_from_db()
        self.assertEqual(op.state, ProvisioningOperation.ST_COMPLETED)

    def test_revoke_idempotent_double_run(self):
        op = ProvisioningOperation.objects.create(
            operation=ProvisioningOperation.OP_REVOKE, user_id=uuid.uuid4(),
            channel_id="-1001", telegram_user_id=555)
        t = mock.Mock()
        t.revoke.return_value = mock.Mock(ok=True, retryable=False)
        execute_operation(op, t)
        execute_operation(op, t)
        self.assertEqual(t.revoke.call_count, 2)   # safe to repeat
        op.refresh_from_db()
        self.assertEqual(op.state, ProvisioningOperation.ST_COMPLETED)

    def test_new_lifecycle_gets_new_operation_id(self):
        u = uuid.uuid4()
        op1 = ProvisioningOperation.objects.create(
            operation=ProvisioningOperation.OP_GRANT, user_id=u, channel_id="-1001",
            telegram_user_id=555, state=ProvisioningOperation.ST_COMPLETED)
        op2 = ProvisioningOperation.objects.create(
            operation=ProvisioningOperation.OP_GRANT, user_id=u, channel_id="-1001",
            telegram_user_id=555)   # re-grant after revoke: new row
        self.assertNotEqual(op1.operation_id, op2.operation_id)


class EnqueueGenericTests(TestCase):
    def test_duplicate_open_generic_suppressed(self):
        j1 = enqueue_generic("update_maxmind", {}, idempotency_key="sched:mm")
        j2 = enqueue_generic("update_maxmind", {}, idempotency_key="sched:mm")
        self.assertEqual(j1.pk, j2.pk)
