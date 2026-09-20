"""Provisioning operation state machine: durable external side effects.

Honest at-least-once with persisted operation state.  The irreducible
window (Telegram executes createChatInviteLink, process dies before the
URL is persisted) is documented: recovery mints ONE replacement link and
checks membership before any re-send.  Revoke is a separate idempotent
machine.
"""

import logging
from django.utils import timezone

from .models import ProvisioningOperation

logger = logging.getLogger(__name__)


class _ProvisionTransport:
    """Pluggable transport (real = ProvisionClient; tests inject a fake)."""


def execute_operation(op: ProvisioningOperation, transport) -> bool:
    """Drive one operation to completion.  Returns True when terminal.

    States pending/creating: no external effect committed yet -> safe rerun.
    created/sent/unknown: reuse persisted invite_link; membership check
    before any re-send.  Revoke: ban is naturally idempotent.
    """
    op.attempts += 1
    if op.operation == ProvisioningOperation.OP_REVOKE:
        return _run_revoke(op, transport)
    return _run_grant(op, transport)


def _run_revoke(op, transport) -> bool:
    result = transport.revoke(op.telegram_user_id, op.channel_id,
                              idempotency_key=op.provision_key)
    if result.ok:
        # A provisioning "success" is NOT proof of revocation.  The bridge
        # maps Telegram "user not found"/"kicked" to already_applied, and its
        # idempotency cache can replay an old success -- either can report
        # success while the user is still in the channel.  Verify with a
        # read-only membership probe before declaring completion.
        member = transport.is_member(op.telegram_user_id, op.channel_id)
        if member is False:
            op.state = ProvisioningOperation.ST_COMPLETED
            op.save(update_fields=["state"])
            return True
        op.state = ProvisioningOperation.ST_FAILED
        op.last_error = (
            "revoke accepted by bot but user is still a member"
            if member is True else
            "revoke accepted by bot but membership could not be verified")
        op.save(update_fields=["state", "last_error"])
        return True  # terminal for this attempt; next reconcile retries
                       # with a fresh revoke operation
    if result.retryable:
        op.state = ProvisioningOperation.ST_FAILED
        op.last_error = result.error_message
        op.save(update_fields=["state", "last_error"])
        return True    # terminal for this attempt; job retry re-enters
    op.state = ProvisioningOperation.ST_FAILED
    op.last_error = result.error_message
    op.save(update_fields=["state", "last_error"])
    return True


def _run_grant(op, transport) -> bool:
    st = ProvisioningOperation

    # Recovery for states with an uncertain send outcome.
    if op.state in (st.ST_SENDING, st.ST_SENT, st.ST_UNKNOWN):
        member = transport.is_member(op.telegram_user_id, op.channel_id)
        if member is True:
            op.state = st.ST_COMPLETED
            op.save(update_fields=["state"])
            return True
        # Not a member: outcome unknown or send failed.  Re-send the SAME
        # persisted link if we have one; never mint a new link merely
        # because the send of the persisted link failed.
        if not op.invite_link:
            # No persisted link (crashed in 'creating'): fall through to
            # mint a new link — bounded, documented irreducible window.
            op.state = st.ST_CREATING
            op.save(update_fields=["state"])
        else:
            return _send_link(op, transport)

    if op.state == st.ST_FAILED:
        if op.invite_link:
            return _send_link(op, transport)
        op.state = st.ST_CREATING
        op.save(update_fields=["state"])

    if op.state in (st.ST_PENDING, st.ST_CREATING):
        # Mark creating BEFORE the side effect (durability).
        op.state = st.ST_CREATING
        op.save(update_fields=["state"])
        # provision_key is unique per operation: the bridge's 24h grant
        # cache is keyed on it, so each NEW lifecycle actually executes the
        # bot's do_grant (unban + fresh invite + DM) instead of receiving a
        # cached already_applied from a previous lifecycle.
        created = transport.create_invite_link(op.telegram_user_id,
                                               op.channel_id,
                                               idempotency_key=op.provision_key)
        if created is None:
            # External outcome unknown (transport error after dispatch).
            op.state = st.ST_UNKNOWN
            op.save(update_fields=["state"])
            return False   # not terminal; job retries -> recovery path
        # Persist link + state in the SAME step, immediately on success.
        # The bridge's do_grant ALREADY delivered the invite DM server-side
        # as part of minting; do NOT _send_link here -- that would double-DM.
        op.invite_link = created
        op.state = st.ST_SENT
        op.save(update_fields=["invite_link", "state"])
        return _confirm_delivered(op, transport)

    if op.state == st.ST_CREATED:
        # Link persisted; DM already delivered server-side by do_grant.
        op.state = st.ST_SENT
        op.save(update_fields=["state"])
        return _confirm_delivered(op, transport)

    return op.state == st.ST_COMPLETED


def _confirm_delivered(op, transport) -> bool:
    """Post-delivery confirmation for server-side DMs (do_grant): mark SENT,
    upgrade to COMPLETED when membership is already visible.  Never sends.
    """
    st = ProvisioningOperation
    if transport.is_member(op.telegram_user_id, op.channel_id) is True:
        op.state = st.ST_COMPLETED
        op.save(update_fields=["state"])
    return True


def _send_link(op, transport) -> bool:
    st = ProvisioningOperation
    op.state = st.ST_SENDING
    op.save(update_fields=["state"])
    sent = transport.send_invite_dm(op.telegram_user_id,
                                  op.channel_id,
                                  op.invite_link)
    if sent is True:
        op.state = st.ST_SENT
        op.save(update_fields=["state"])
        # Final confirmation via membership (user may join immediately).
        member = transport.is_member(op.telegram_user_id, op.channel_id)
        if member is True:
            op.state = st.ST_COMPLETED
            op.save(update_fields=["state"])
            return True
        return False   # sent, not yet joined; convergence completes later
    if sent is False:
        op.state = st.ST_FAILED
        op.last_error = "send_message known failure"
        op.save(update_fields=["state", "last_error"])
        return True    # terminal-ish; retry re-sends SAME link
    # None: send outcome unknown.
    op.state = st.ST_UNKNOWN
    op.save(update_fields=["state"])
    return False
