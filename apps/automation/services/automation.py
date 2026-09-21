"""G3 automation: trigger rules on events, fire delayed runs with idempotent send."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.emailing.models import Delivery
from apps.emailing.services.render import render_version
from apps.events.models import Event
from apps.growth.models import can_send_marketing

from ..models import AutomationRule, AutomationRun


def trigger_rules_for_event(event):
    """Enqueue an AutomationRun for every enabled rule matching this event.

    Idempotent: unique (rule, event) means duplicate event delivery or
    re-processing schedules at most one run per rule.
    """
    if event.user_id is None:
        return 0
    rules = AutomationRule.objects.filter(
        trigger_event_type=event.event_type, enabled=True)
    count = 0
    for rule in rules:
        run, created = AutomationRun.objects.get_or_create(
            rule=rule, event=event,
            defaults={
                "user_id": event.user_id,
                "scheduled_at": timezone.now() + timedelta(minutes=rule.delay_minutes),
            },
        )
        if created:
            count += 1
    return count


def fire_due_runs(now=None, limit=100):
    """Durable-sweep entry point: fire PENDING runs whose time has come.

    Atomically claims due runs (conditional UPDATE), then for each: re-check
    eligibility AT FIRE TIME (consent/suppression), render the frozen template,
    and create the idempotent Delivery.  Skips ineligible runs.
    Returns {"fired": n, "skipped": n}.
    """
    now = now or timezone.now()
    ids = list(AutomationRun.objects.filter(
        status=AutomationRun.Status.PENDING, scheduled_at__lte=now
    ).values_list("pk", flat=True)[:limit])
    if not ids:
        return {"fired": 0, "skipped": 0}
    # Claim only rows that are STILL pending right now; a concurrent sweeper
    # that already claimed them gets 0 rows back (atomic conditional claim).
    claimed = AutomationRun.objects.filter(
        pk__in=ids, status=AutomationRun.Status.PENDING).update(status=AutomationRun.Status.FIRED)
    runs = list(AutomationRun.objects.filter(pk__in=ids, status=AutomationRun.Status.FIRED,
                                             fired_at__isnull=True).select_related(
        "rule", "rule__template_version", "rule__template_version__template", "user"))
    fired = skipped = 0
    for run in runs:
        result = _fire_run(run)
        if result == "fired":
            fired += 1
        elif result == "skipped":
            skipped += 1
    return {"fired": fired, "skipped": skipped}


def _fire_run(run):
    """Fire a single claimed run.  Returns 'fired'|'skipped'|'failed'."""
    from django.db import transaction
    rule = run.rule
    user = run.user
    email = user.email if user else None
    if not email:
        return _mark(run, AutomationRun.Status.SKIPPED, "no email")
    # Eligibility at FIRE TIME: transactional (marketing) requires opt-in and
    # not suppressed.  A user who unsubscribed during the delay is skipped here.
    is_transactional = rule.template_version.template.kind == "transactional"
    if not is_transactional and not can_send_marketing(user, email):
        return _mark(run, AutomationRun.Status.SKIPPED, "ineligible (opt-out/suppressed)")

    context = {"first_name": (user.first_name or user.username or "")}
    subject, html, plain = render_version(rule.template_version, context)
    delivery, _ = Delivery.objects.get_or_create(
        idempotency_key=f"automation:{run.rule_id}:{run.id}",
        defaults={
            "template_version": rule.template_version,
            "user": user,
            "to_email": email,
            "kind": rule.template_version.template.kind,
            "subject": subject,
            "state": Delivery.State.SENT,   # stub provider accepts synchronously
            "provider": "stub",
            "provider_message_id": f"stub-auto-{run.id}",
            "sent_at": timezone.now(),
        },
    )
    run.delivery = delivery
    run.fired_at = timezone.now()
    run.status = AutomationRun.Status.FIRED
    run.save(update_fields=["delivery", "fired_at", "status"])
    return "fired"


def _mark(run, status, error=""):
    run.status = status
    run.error = error[:500]
    run.fired_at = timezone.now()
    run.save(update_fields=["status", "error", "fired_at"])
    return "skipped" if status == AutomationRun.Status.SKIPPED else "failed"
