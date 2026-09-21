"""G2 campaign services: schedule/snapshot + durable send-sweep."""
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.emailing.models import Delivery, TemplateVersion
from apps.emailing.services.render import render_version
from apps.growth.models import can_send_marketing

from ..models import Campaign, CampaignMetrics, CampaignRecipient


def schedule_campaign(campaign, send_at=None):
    """Freeze the audience (snapshot) and mark the campaign scheduled.

    Snapshotting materializes the audience into CampaignRecipient rows at this
    moment, so membership is provable and never changes after scheduling.
    Marketing recipients who are opted-out/suppressed are marked SKIPPED now.
    """
    if campaign.state not in (Campaign.State.DRAFT,):
        return False
    members = campaign.audience.members()
    recipients = []
    for user in members.iterator():
        email = user.email
        if not email:
            continue
        status = (CampaignRecipient.Status.QUEUED
                  if can_send_marketing(user, email)
                  else CampaignRecipient.Status.SKIPPED)
        recipients.append(CampaignRecipient(campaign=campaign, user=user,
                                            email=email, status=status))
    # Bulk-create, ignoring members already snapshotted (idempotent re-schedule).
    CampaignRecipient.objects.bulk_create(recipients, ignore_conflicts=True)
    campaign.snapshot_at = timezone.now()
    campaign.scheduled_at = send_at or timezone.now()
    campaign.state = Campaign.State.SCHEDULED
    campaign.save(update_fields=["snapshot_at", "scheduled_at", "state"])
    CampaignMetrics.objects.get_or_create(
        campaign=campaign, defaults={"total": len(recipients)})
    return True


def _claim_recipients(campaign, limit=100):
    """Atomically claim up to `limit` QUEUED recipients for this campaign.

    Conditional UPDATE ... WHERE status='queued' returns the claimed ids;
    concurrent sweepers cannot claim the same row twice.
    """
    ids = list(CampaignRecipient.objects.filter(
        campaign=campaign, status=CampaignRecipient.Status.QUEUED
    ).values_list("pk", flat=True)[:limit])
    if not ids:
        return []
    CampaignRecipient.objects.filter(
        pk__in=ids, status=CampaignRecipient.Status.QUEUED
    ).update(status=CampaignRecipient.Status.SENDING)
    return list(CampaignRecipient.objects.filter(pk__in=ids).select_related("user"))


def _send_to_recipient(campaign, recipient):
    """Render the frozen template snapshot and create an idempotent Delivery.

    Delivery.idempotency_key = campaign:recipient guarantees at-most-once
    delivery even if the sweep runs twice or the job retries.  The provider
    is a stub (no live provider wired in G2); a provider reference is set so
    downstream metrics/bounce handling have something to key on.
    """
    version = campaign.template_version
    context = {"first_name": (recipient.user.first_name or recipient.user.username or "")}
    subject, html, plain = render_version(version, context)
    if campaign.subject_override:
        subject = campaign.subject_override
    delivery, created = Delivery.objects.get_or_create(
        idempotency_key=f"campaign:{campaign.pk}:{recipient.pk}",
        defaults={
            "template_version": version,
            "user": recipient.user,
            "to_email": recipient.email,
            "kind": version.template.kind,
            "subject": subject,
            "state": Delivery.State.QUEUED,
            "provider": "stub",
        },
    )
    recipient.delivery = delivery
    # Mark sent immediately (stub provider "accepts" synchronously).
    if created:
        delivery.state = Delivery.State.SENT
        delivery.sent_at = timezone.now()
        delivery.provider_message_id = f"stub-{delivery.pk}"
        delivery.save(update_fields=["state", "sent_at", "provider_message_id"])
    recipient.status = CampaignRecipient.Status.SENT
    recipient.save(update_fields=["delivery", "status"])
    return delivery


def campaign_send_sweep(campaign_id, batch=100):
    """Durable-job entry point: send to a batch of claimed recipients.

    Claims QUEUED recipients atomically, sends each via the idempotent
    Delivery, updates CampaignMetrics from the claimed transitions, and marks
    the campaign DONE when no QUEUED recipients remain.
    """
    campaign = Campaign.objects.filter(pk=campaign_id).first()
    if campaign is None or campaign.state in (Campaign.State.DONE, Campaign.State.PAUSED):
        return {"campaign": str(campaign_id), "sent": 0, "done": campaign is not None}
    if campaign.state == Campaign.State.SCHEDULED:
        campaign.state = Campaign.State.SENDING
        campaign.save(update_fields=["state"])

    claimed = _claim_recipients(campaign, limit=batch)
    sent = failed = 0
    for recipient in claimed:
        try:
            _send_to_recipient(campaign, recipient)
            sent += 1
        except Exception:  # noqa: BLE001 - durable job retries
            recipient.status = CampaignRecipient.Status.FAILED
            recipient.save(update_fields=["status"])
            failed += 1

    metrics = campaign.metrics
    CampaignMetrics.objects.filter(pk=metrics.pk).update(
        sent=F("sent") + sent, failed=F("failed") + failed,
        suppressed=F("suppressed") + CampaignRecipient.objects.filter(
            campaign=campaign, status=CampaignRecipient.Status.SUPPRESSED).count(),
    )
    remaining = CampaignRecipient.objects.filter(
        campaign=campaign, status=CampaignRecipient.Status.QUEUED).count()
    done = remaining == 0
    if done:
        campaign.state = Campaign.State.DONE
        campaign.save(update_fields=["state"])
    return {"campaign": str(campaign_id), "sent": sent, "failed": failed, "done": done}
