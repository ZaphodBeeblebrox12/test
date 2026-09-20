"""Reconcile bridge: durable ProvisioningOperation per (user, channel, intent).

The existing diff engine (compute_target_access vs UserChannelAssignment)
decides WHICH operations are needed.  Each needed operation gets ONE open
ProvisioningOperation row; retries reuse its id/link; a NEW lifecycle
(grant after revoke completed) creates a NEW row.
"""

import logging
from datetime import timedelta

from django.db.models import Q

from django.utils import timezone

from apps.bot_integration.models import (
    BotAccessAudit, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.reconcile import (
    _audit, _control_channel_guard, compute_target_access)
from apps.jobs.models import ProvisioningOperation
from apps.jobs.operations import execute_operation

logger = logging.getLogger(__name__)

# Persistent resend throttle for membership-recovery invites (6 hours).
INVITE_RESEND_THROTTLE = timedelta(hours=6)


class ProvisionTransport:
    """Transport adapter over the existing ProvisionClient."""

    def __init__(self, client=None):
        from apps.bot_integration.services.provision_client import ProvisionClient
        self.client = client or ProvisionClient()

    def create_invite_link(self, telegram_user_id, channel_id,
                           idempotency_key=None):
        # The provision API mints the link as part of grant; we surface the
        # outcome via grant's detail.  Simplified: grant performs the whole
        # Telegram side; here we drive the state machine with the client.
        # idempotency_key is the ProvisioningOperation.provision_key: each
        # NEW grant lifecycle must actually reach the bot's do_grant (which
        # unbans).  The bridge caches grant results by this key for 24h, so
        # a static per-user/channel key would return a cached already_applied
        # and silently skip the unban.
        res = self.client.grant(telegram_user_id, channel_id,
                                idempotency_key=idempotency_key)
        if res.ok:
            return (res.detail or {}).get("invite_link", "")
        return None

    def send_invite_dm(self, telegram_user_id, channel_id, invite_link):
        # Initial grants DM the link server-side inside do_grant; this method
        # is the DELIVERY path for recovery resends (and SEND/UNKNOWN retries)
        # of a PERSISTED invite link: it must actually reach the user through
        # the bot's existing contract DM path.  A failure here is a failure
        # to send -- never report success without the bot accepting it.
        res = self.client.resend_invite(telegram_user_id, channel_id,
                                        invite_link)
        return bool(res.ok)

    def is_member(self, telegram_user_id, channel_id):
        # True = member; False = definitely not a member; None = membership
        # could not be determined (transport/Telegram error).  "Unknown" is
        # NEVER collapsed into "not a member".
        res = self.client.check_membership(telegram_user_id, channel_id)
        if res.ok:
            return bool((res.detail or {}).get("member"))
        return None

    def revoke(self, telegram_user_id, channel_id, idempotency_key=None):
        # Must forward the key: without it the bridge falls back to its
        # static per-user/channel cache key, and a revoke within 24h of any
        # earlier revoke would return a cached already_applied WITHOUT
        # executing banChatMember.
        return self.client.revoke(telegram_user_id, channel_id,
                                  idempotency_key=idempotency_key)


def _get_or_create_operation(user_id, channel_id, operation, telegram_user_id):
    """Reuse the OPEN operation for this intent; else create a new one."""
    open_states = [ProvisioningOperation.ST_PENDING, ProvisioningOperation.ST_CREATING,
                   ProvisioningOperation.ST_CREATED, ProvisioningOperation.ST_SENDING,
                   ProvisioningOperation.ST_SENT, ProvisioningOperation.ST_UNKNOWN]
    op = (ProvisioningOperation.objects
          .filter(user_id=user_id, channel_id=channel_id, operation=operation,
                  state__in=open_states)
          .order_by("id").first())
    if op is not None:
        return op
    return ProvisioningOperation.objects.create(
        user_id=user_id, channel_id=channel_id, operation=operation,
        telegram_user_id=telegram_user_id)


def _finalize_grant(user_id, channel_id):
    UserChannelAssignment.objects.get_or_create(
        user_id=user_id, platform="telegram", external_id=channel_id,
        defaults={"is_active": True})
    UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", external_id=channel_id,
        is_active=False).update(is_active=True, revoked_at=None)
    _audit(user_id, "grant", "telegram", channel_id, True, "")


def _finalize_revoke(user_id, channel_id):
    UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", external_id=channel_id,
        is_active=True).update(is_active=False, revoked_at=timezone.now())
    _audit(user_id, "revoke", "telegram", channel_id, True, "")


def _stamp_invite_sent(user_id, channel_id):
    """Stamp the persistent resend throttle; call ONLY when an invite was
    actually (re)sent (or plausibly sent in the ST_UNKNOWN window)."""
    UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", external_id=channel_id,
    ).update(last_invite_sent_at=timezone.now())


def _supersede_open_grants(user_id, channel_id):
    """Close any OPEN grant operations for this user/channel.

    Called when a revoke completes.  ProvisioningOperation's documented
    invariant is "a NEW grant after a completed revoke gets a NEW row";
    without this, _get_or_create_operation would reuse a stale ST_SENT
    grant whose recovery path only re-sends the persisted invite and never
    reaches the bot's unban.  Closed ops are terminal (failed/superseded),
    so re-grants create a fresh lifecycle.
    """
    ProvisioningOperation.objects.filter(
        user_id=user_id, channel_id=channel_id,
        operation=ProvisioningOperation.OP_GRANT,
        state__in=[ProvisioningOperation.ST_PENDING,
                   ProvisioningOperation.ST_CREATING,
                   ProvisioningOperation.ST_CREATED,
                   ProvisioningOperation.ST_SENDING,
                   ProvisioningOperation.ST_SENT,
                   ProvisioningOperation.ST_UNKNOWN],
    ).update(state=ProvisioningOperation.ST_FAILED,
             last_error="superseded_by_revoke")


def _release_resend_claim(assignment_pk, claimed_at, previous):
    """Return a claimed resend window without clobbering a newer claim."""
    qs = UserChannelAssignment.objects.filter(
        pk=assignment_pk, last_invite_sent_at=claimed_at)
    qs.update(last_invite_sent_at=previous)


def _recover_membership(user_id, telegram_user_id, channel_id, transport):
    """Recover access for an entitled user who is no longer a channel member.

    The assignment stays active throughout: is_active tracks the
    authorized/provisioned ENTITLEMENT, not live membership.  A read-only
    membership probe runs BEFORE any invite; an undetermined result (None)
    never triggers a resend.  Resends reuse the persisted open
    ProvisioningOperation/invite link where one exists; only when no
    reusable operation exists is a new one created.

    The 6h resend throttle is an ATOMIC CLAIM (one conditional UPDATE both
    checks and takes the window), so concurrent reconciles -- e.g. an
    interactive test harness and the background jobs worker both running
    run_user_reconcile for the same user -- can never both pass the check.
    The claim is released (timestamp restored) unless an invite actually
    went out (SENT) or plausibly went out (UNKNOWN window).
    """
    assignment = UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", external_id=channel_id,
        is_active=True).first()
    if assignment is None:
        return
    now = timezone.now()
    cutoff = now - INVITE_RESEND_THROTTLE
    previous_ts = assignment.last_invite_sent_at
    claimed = UserChannelAssignment.objects.filter(
        pk=assignment.pk, is_active=True,
    ).filter(
        Q(last_invite_sent_at__isnull=True)
        | Q(last_invite_sent_at__lt=cutoff),
    ).update(last_invite_sent_at=now)
    if not claimed:
        return  # another reconcile claimed the window first; do not resend
    member = transport.is_member(telegram_user_id, channel_id)
    if member is not False:
        # True: nothing to recover.  None: undetermined -- never send.
        # Either way no invite went out: release the claim.
        _release_resend_claim(assignment.pk, now, previous_ts)
        return
    op = _get_or_create_operation(user_id, channel_id,
                                  ProvisioningOperation.OP_GRANT, telegram_user_id)
    execute_operation(op, transport)
    if op.state in (ProvisioningOperation.ST_SENT, ProvisioningOperation.ST_UNKNOWN):
        # Keep the claim: an invite actually went out (SENT), or plausibly
        # went out (UNKNOWN = documented irreducible window).
        pass
    else:
        _release_resend_claim(assignment.pk, now, previous_ts)
    if op.state == ProvisioningOperation.ST_COMPLETED:
        _audit(user_id, "grant", "telegram", channel_id, True,
               "membership recovered")
    elif op.state == ProvisioningOperation.ST_FAILED:
        _audit(user_id, "grant", "telegram", channel_id, False,
               op.last_error[:500])


def run_user_reconcile(user_id: int, transport=None) -> None:
    """Job handler: diff -> operations -> state machine -> finalization.

    Idempotent and convergence-friendly: re-runs recompute the diff; open
    operations are reused; terminal outcomes re-apply harmlessly.
    """
    from apps.bot_integration.models import TelegramAccount
    account = TelegramAccount.objects.filter(user_id=user_id, is_active=True).first()
    if not account or not account.telegram_user_id:
        return
    tg_id = account.telegram_user_id

    target = compute_target_access(user_id)
    existing = set(UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", is_active=True,
    ).values_list("external_id", flat=True))

    wanted = set(target.telegram_ids) if target.has_plan else set()
    guard = _control_channel_guard
    transport = transport or ProvisionTransport()

    # Revokes first (drop access), then grants.
    for channel_id in sorted(existing - wanted):
        if guard(channel_id):
            _audit(user_id, "revoke", "telegram", channel_id, False, guard(channel_id))
            continue
        op = _get_or_create_operation(user_id, channel_id,
                                      ProvisioningOperation.OP_REVOKE, tg_id)
        execute_operation(op, transport)
        if op.state == ProvisioningOperation.ST_COMPLETED:
            _finalize_revoke(user_id, channel_id)
            # A completed revoke ENDS the grant lifecycle: a later re-grant
            # must run the FULL grant path (ProvisionClient.grant ->
            # /provision/v1/access -> bot do_grant), which performs the
            # unban.  An old ST_SENT grant must NOT be reused for that.
            _supersede_open_grants(user_id, channel_id)
        elif op.state == ProvisioningOperation.ST_FAILED:
            _audit(user_id, "revoke", "telegram", channel_id, False, op.last_error[:500])

    for channel_id in sorted(wanted - existing):
        if guard(channel_id):
            _audit(user_id, "grant", "telegram", channel_id, False, guard(channel_id))
            continue
        op = _get_or_create_operation(user_id, channel_id,
                                      ProvisioningOperation.OP_GRANT, tg_id)
        execute_operation(op, transport)
        if op.state in (ProvisioningOperation.ST_COMPLETED,
                        ProvisioningOperation.ST_SENT):
            # COMPLETED: the user joined.  SENT: the invite was created and
            # the DM delivered, but the user has not joined yet.  Both mean
            # the entitlement is provisioned, so the assignment becomes/stays
            # active: is_active means "entitled/authorized", NOT "currently a
            # Telegram member".  The operation may legitimately remain
            # ST_SENT while membership is pending; later runs converge it.
            _finalize_grant(user_id, channel_id)
            if op.state == ProvisioningOperation.ST_SENT:
                _stamp_invite_sent(user_id, channel_id)
        elif op.state == ProvisioningOperation.ST_FAILED:
            _audit(user_id, "grant", "telegram", channel_id, False, op.last_error[:500])

    # Membership recovery: entitlement AND active assignment exist, but the
    # user may no longer be in the channel.  Recover access without touching
    # the assignment (the entitlement still exists).
    for channel_id in sorted(wanted & existing):
        if guard(channel_id):
            continue
        _recover_membership(user_id, tg_id, channel_id, transport)

    account.last_synced_at = timezone.now()
    account.save(update_fields=["last_synced_at"])
