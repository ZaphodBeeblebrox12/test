"""Lightweight job worker: management command + claim/complete primitives.

    python manage.py runjobs [--loop] [--sleep N] [--once]

SQLite: single worker, short transactions, transient locked-retry.
PostgreSQL later: swap claim() for FOR UPDATE SKIP LOCKED behind the same
API.  Lock order everywhere: ReconcileState -> Job.
"""

import logging
import time
from django.core.management.base import BaseCommand
from django.db import OperationalError, transaction
from django.utils import timezone

from .enqueue import MAX_CONVERGENCE_ITERATIONS, _retry_locked
from .models import Job, ReconcileState

logger = logging.getLogger(__name__)

REAPER_STALE_SECONDS = 300
BACKOFF_BASE_SECONDS = 30


def reaper_reset():
    """Reset jobs stuck 'running' beyond the staleness window."""
    cutoff = timezone.now() - timezone.timedelta(seconds=REAPER_STALE_SECONDS)
    with transaction.atomic():
        return _retry_locked(lambda: Job.objects.filter(
            status=Job.STATUS_RUNNING, locked_at__lt=cutoff).update(
            status=Job.STATUS_PENDING, locked_at=None))


@transaction.atomic
def claim_next(kind=None):
    """Claim the oldest due job.  Portable: row update serializes claimants
    on SQLite (writer lock) and via select_for_update on PostgreSQL."""
    qs = (Job.objects
          .filter(status=Job.STATUS_PENDING, next_attempt_at__lte=timezone.now())
          .order_by("id"))
    if kind:
        qs = qs.filter(kind=kind)
    job = _retry_locked(lambda: qs.select_for_update().first())
    if job is None:
        return None
    job.status = Job.STATUS_RUNNING
    job.locked_at = timezone.now()
    job.attempts += 1
    job.save(update_fields=["status", "locked_at", "attempts"])
    return job


def _complete(job):
    """Terminal success: release the open-job idempotency key."""
    job.status = Job.STATUS_SUCCEEDED
    job.idempotency_key = None
    job.locked_at = None
    job.save(update_fields=["status", "idempotency_key", "locked_at"])


def _fail(job, exc):
    job.locked_at = None
    if job.attempts >= job.max_attempts:
        job.status = Job.STATUS_FAILED
        job.idempotency_key = None
        job.save(update_fields=["status", "idempotency_key", "locked_at"])
        logger.error("[jobs] job %s failed permanently: %s", job.pk, exc)
    else:
        job.status = Job.STATUS_PENDING
        job.next_attempt_at = timezone.now() + timezone.timedelta(
            seconds=BACKOFF_BASE_SECONDS * job.attempts)
        job.save(update_fields=["status", "next_attempt_at", "locked_at"])
        logger.warning("[jobs] job %s attempt %s failed, retry scheduled: %s",
                       job.pk, job.attempts, exc)


@transaction.atomic
def _check_and_close_or_adopt(job):
    """Completion gate.  Lock order: ReconcileState first, then Job.

    Returns (closed, job).  If the user's desired-state version advanced
    past what this job requested, adopt the newer version and return
    closed=False so the caller re-runs the body.  Never declares success
    on a stale version.
    """
    if job.kind == "reconcile":
        user_id = job.payload["user_id"]
        # Lock order: ReconcileState BEFORE Job.
        state = (ReconcileState.objects
                 .filter(user_id=user_id).select_for_update().first())
        latest = state.version if state else 0
        job = Job.objects.select_for_update().get(pk=job.pk)
        if latest > job.requested_version:
            job.requested_version = latest
            job.save(update_fields=["requested_version"])
            return False, job
    _complete(job)
    return True, job


def process_reconcile_job(job, run_body, iteration=0):
    """Run reconcile body with bounded convergence to the latest version.

    run_body(user_id) performs grants/revokes via the operation state machine.
    """
    if iteration >= MAX_CONVERGENCE_ITERATIONS:
        # Convergence limit reached: leave a durable pending job for the
        # next attempt rather than silently accepting a stale version.
        with transaction.atomic():
            locked = Job.objects.select_for_update().get(pk=job.pk)
            locked.status = Job.STATUS_PENDING
            locked.next_attempt_at = timezone.now() + timezone.timedelta(
                seconds=BACKOFF_BASE_SECONDS)
            locked.locked_at = None
            locked.save(update_fields=["status", "next_attempt_at", "locked_at"])
        logger.error("[jobs] job %s hit convergence limit; left pending", job.pk)
        return

    try:
        run_body(job.payload["user_id"])
    except Exception as exc:
        with transaction.atomic():
            _fail(Job.objects.select_for_update().get(pk=job.pk), exc)
        return

    with transaction.atomic():
        closed, job = _check_and_close_or_adopt(Job.objects.get(pk=job.pk))
    if not closed:
        process_reconcile_job(job, run_body, iteration + 1)


HANDLERS = {}  # kind -> callable(payload_dict, job) ; registered at startup


def process_job(job):
    if job.kind == "reconcile":
        from apps.bot_integration.reconcile_jobs import run_user_reconcile
        return process_reconcile_job(job, run_user_reconcile)
    handler = HANDLERS.get(job.kind)
    if handler is None:
        with transaction.atomic():
            _fail(Job.objects.select_for_update().get(pk=job.pk),
                  ValueError(f"no handler for kind {job.kind!r}"))
        return
    try:
        handler(job.payload, job)
    except Exception as exc:
        with transaction.atomic():
            _fail(Job.objects.select_for_update().get(pk=job.pk), exc)
        return
    with transaction.atomic():
        _complete(Job.objects.select_for_update().get(pk=job.pk))


class Command(BaseCommand):
    help = "Run durable jobs (replaces the Celery worker)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--sleep", type=float, default=2.0)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--kind", default=None)

    def handle(self, *args, **opts):
        from .handlers import register_builtin_handlers
        register_builtin_handlers()
        while True:
            try:
                reaper_reset()
                job = claim_next(kind=opts.get("kind"))
                if job is not None:
                    logger.info("[jobs] claimed %s", job)
                    process_job(job)
                elif not opts["loop"]:
                    break
                else:
                    time.sleep(opts["sleep"])
            except OperationalError as exc:
                if "locked" in str(exc).lower():
                    time.sleep(0.2)
                    continue
                raise
            if opts["once"]:
                break
