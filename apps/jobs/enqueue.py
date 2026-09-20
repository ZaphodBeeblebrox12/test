"""Transactional enqueue layer for the job system.

LOCK ORDER (documented, consistent everywhere): ReconcileState -> Job.
Every transaction that touches both locks ReconcileState FIRST.

SQLite: one worker, short transactions, WAL, transient "database is locked"
retries.  PostgreSQL: row locks via select_for_update().  The application
API (enqueue_reconcile / process_job) is identical on both.
"""

import logging
import time
from django.db import OperationalError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Job, ReconcileState

logger = logging.getLogger(__name__)

LOCK_RETRY_ATTEMPTS = 4
LOCK_RETRY_BASE_SECONDS = 0.2
MAX_CONVERGENCE_ITERATIONS = 10


def _retry_locked(fn, *args, **kwargs):
    """Run fn, retrying transient SQLite 'database is locked' errors."""
    for attempt in range(LOCK_RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except OperationalError as exc:
            is_locked = "locked" in str(exc).lower() or "database is locked" in str(exc).lower()
            if not is_locked or attempt == LOCK_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(LOCK_RETRY_BASE_SECONDS * (2 ** attempt))
    return None


@transaction.atomic
@transaction.atomic
def enqueue_reconcile(user_id: int, reason: str = "") -> Job:
    """Coalescing transactional enqueue of a reconciliation job.

    Lock order: ReconcileState -> Job (held throughout one transaction).
    The pre-check inside the same transaction (while holding the version
    row) means the insert almost never hits the partial-unique constraint;
    the rare concurrent race falls through the nested-savepoint path.
    """
    from django.db import connection as _conn
    _lock = {"sqlite3": lambda qs: qs}.get(_conn.vendor, lambda qs: qs.select_for_update())
    key = f"reconcile:{user_id}"

    updated = _retry_locked(lambda: _lock(ReconcileState.objects)
                            .filter(user_id=user_id).update(version=F("version") + 1))
    if updated == 0:
        _retry_locked(lambda: ReconcileState.objects.create(user_id=user_id, version=1))
    version = _retry_locked(lambda: _lock(ReconcileState.objects)
                            .get(user_id=user_id).version)

    existing = _retry_locked(lambda: _lock(Job.objects)
                             .filter(idempotency_key=key, status__in=Job.OPEN_STATUSES)
                             .first())
    if existing is not None:
        if version > existing.requested_version:
            existing.requested_version = version
            existing.next_attempt_at = timezone.now()
            existing.save(update_fields=["requested_version", "next_attempt_at"])
        return existing

    try:
        with transaction.atomic(savepoint=True):
            return Job.objects.create(
                kind="reconcile", payload={"user_id": str(user_id), "reason": reason},
                idempotency_key=key, requested_version=version)
    except IntegrityError:
        pass

    job = _retry_locked(lambda: _lock(Job.objects.filter(idempotency_key=key)).get())
    if version > job.requested_version:
        job.requested_version = version
        job.save(update_fields=["requested_version"])
    return job


@transaction.atomic
def enqueue_generic(kind: str, payload: dict, idempotency_key=None,
                    delay_seconds: int = 0) -> Job:
    """Non-reconcile enqueue (sweep/geoip/referrals) with simple suppression
    of duplicate OPEN jobs of the same kind+key."""
    next_at = timezone.now() + timezone.timedelta(seconds=delay_seconds)
    key = idempotency_key or f"{kind}:{hash(frozenset(payload.items())) if payload else ''}"
    existing = _retry_locked(lambda: (Job.objects
                                      .filter(idempotency_key=key,
                                              status__in=Job.OPEN_STATUSES)
                                      .select_for_update().first()))
    if existing is not None:
        return existing
    try:
        with transaction.atomic(savepoint=True):
            return Job.objects.create(kind=kind, payload=payload,
                                      idempotency_key=key, next_attempt_at=next_at)
    except IntegrityError:
        pass
    return _retry_locked(lambda: Job.objects.get(idempotency_key=key))
