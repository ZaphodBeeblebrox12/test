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
        op.state = ProvisioningOperation.ST_COMPLETED
        op.save(update_fields=["state"])
        return True
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
        created = transport.create_invite_link(op.telegram_user_id, op.channel_id)
        if created is None:
            # External outcome unknown (transport error after dispatch).
            op.state = st.ST_UNKNOWN
            op.save(update_fields=["state"])
            return False   # not terminal; job retries -> recovery path
        # Persist link + state in the SAME step, immediately on success.
        op.invite_link = created
        op.state = st.ST_CREATED
        op.save(update_fields=["invite_link", "state"])
        return _send_link(op, transport)

    if op.state == st.ST_CREATED:
        return _send_link(op, transport)

    return op.state == st.ST_COMPLETED


def _send_link(op, transport) -> bool:
    st = ProvisioningOperation
    op.state = st.ST_SENDING
    op.save(update_fields=["state"])
    sent = transport.send_invite_dm(op.telegram_user_id, op.invite_link)
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
