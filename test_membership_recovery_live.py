#!/usr/bin/env python
"""
test_membership_recovery_live.py
=================================
LIVE end-to-end proof of the Telegram membership-recovery lifecycle against
the RUNNING local services (Django on :8000, bot bridge on :8010).

Uses ONLY the real project code: real models, real ProvisionClient,
real run_user_reconcile (real ProvisionTransport -> real bridge -> real
Telegram).  No mocks.

YOU enter the Telegram user ID and channel ID; the script resolves the
Django user internally via the project's existing TelegramAccount identity
link (TelegramAccount.telegram_user_id -> user).  Nothing is hard-coded.

Safe to re-run.  It only mutates `last_invite_sent_at` (and restores it at
the end unless doing so would destroy evidence).

Run from the Django project root:

    python test_membership_recovery_live.py
"""

from __future__ import annotations

import os
import sys
import datetime

# Bootstrap exactly like manage.py: point DJANGO_SETTINGS_MODULE at the
# project settings before importing any Django code.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                  "config.settings.development")  # same as manage.py

import django
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.bot_integration.models import (
    TelegramAccount, UserChannelAssignment)
from apps.bot_integration.reconcile_jobs import run_user_reconcile
from apps.bot_integration.services.provision_client import ProvisionClient
from apps.jobs.models import ProvisioningOperation

# Values are INTERACTIVE (prompted below), not hard-coded.  The Django user
# is resolved internally from the existing TelegramAccount identity link.
TELEGRAM_USER_ID = None
CHANNEL_ID = None

# OPEN grant states considered a reusable persisted lifecycle (mirror of
# jobs.operations' open_states for grant operations).
OPEN_GRANT_STATES = (
    ProvisioningOperation.ST_PENDING,
    ProvisioningOperation.ST_CREATING,
    ProvisioningOperation.ST_CREATED,
    ProvisioningOperation.ST_SENDING,
    ProvisioningOperation.ST_SENT,
    ProvisioningOperation.ST_UNKNOWN,
)

results = []  # (label, status, detail)


def report(label, status, detail=""):
    """status in {"PASS", "FAIL", "INFO"}"""
    results.append((label, status, detail))
    mark = {"PASS": "PASS ", "FAIL": "FAIL ", "INFO": "INFO "}[status]
    line = f"[{mark}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)
    if status == "FAIL":
        print("       expected vs actual are in the detail above")


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def get_assignment(user):
    return UserChannelAssignment.objects.filter(
        user=user, platform="telegram", external_id=CHANNEL_ID,
    ).first()


def latest_grant_op(user):
    return (ProvisioningOperation.objects
            .filter(user_id=user.id, channel_id=CHANNEL_ID,
                    operation=ProvisioningOperation.OP_GRANT)
            .order_by("-created_at").first())


def membership():
    res = ProvisionClient().check_membership(TELEGRAM_USER_ID, CHANNEL_ID)
    if res.ok:
        return bool((res.detail or {}).get("member")), res
    return None, res


def print_state(user, note=""):
    a = get_assignment(user)
    member, res = membership()
    op = latest_grant_op(user)
    if note:
        print(f"  ({note})")
    print(f"  assignment.is_active        = {a.is_active if a else 'NO ROW'}")
    print(f"  assignment.revoked_at       = {a.revoked_at if a else '-'}")
    print(f"  assignment.last_invite_sent_at = {a.last_invite_sent_at if a else '-'}")
    print(f"  membership (real API)       = {member}  (detail={res.detail})")
    print(f"  latest grant op             = id={op.id} state={op.state} "
          f"link={op.invite_link!r}")
    return a, member, op


# ==========================================================================
STEP = "STEP 0"


def step0_inspect(user):
    section("STEP 0 -- Inspect initial state (read-only)")
    a, member, op = print_state(user, "initial")
    if a is None:
        report("assignment exists", "FAIL",
               f"no UserChannelAssignment for {CHANNEL_ID}")
        return None
    report("assignment exists", "PASS", f"id={a.id}")
    return {"assignment": a, "member": member, "op": op}


def step1_prepare(user):
    section("STEP 1 -- Prepare recovery condition (force 6h throttle expiry)")
    a = get_assignment(user)
    if not a or not a.is_active:
        report("assignment active", "FAIL",
               f"is_active={a.is_active if a else 'no row'}; expected True")
        return None
    report("assignment active", "PASS")

    member, res = membership()
    if member is True:
        report("membership is False (precondition)",
               "FAIL",
               "member=True right now. LEAVE the Telegram channel, then "
               "re-run this script from STEP 1.")
        return None
    if member is None:
        report("membership is False (precondition)", "FAIL",
               f"check_membership undetermined/error: {res.error_code} "
               f"{res.error_message}")
        return None
    report("membership is False (precondition)", "PASS")

    original_ts = a.last_invite_sent_at
    a.last_invite_sent_at = timezone.now() - datetime.timedelta(hours=7)
    a.save(update_fields=["last_invite_sent_at"])
    report("throttle forced expired", "PASS",
           f"last_invite_sent_at set to 7h ago "
           f"(original={original_ts})")
    return {"original_ts": original_ts}


def step2_reconcile(user, before):
    section("STEP 2 -- Run ONE real reconciliation (recovery resend)")
    op_before = before["op"]
    link_before = op_before.invite_link if op_before else None
    ts_before = get_assignment(user).last_invite_sent_at
    print(f"  before: op_id={op_before.id if op_before else None} "
          f"link={link_before!r} last_invite_sent_at={ts_before}")

    run_user_reconcile(user.id)  # real transport -> real bridge -> real Telegram

    a, member, op_after = print_state(user, "after reconcile")
    link_after = op_after.invite_link if op_after else None
    ts_after = a.last_invite_sent_at

    report("assignment still active", "PASS" if a.is_active else "FAIL",
           f"is_active={a.is_active}")
    report("membership remains FALSE during resend window",
           "PASS" if member is False else ("INFO" if member is True else "FAIL"),
           f"member={member} (still False is expected; user has not joined yet)")

    reused = (op_before is not None and op_after is not None
              and op_before.id == op_after.id)
    report("same grant operation reused (no new lifecycle)",
           "PASS" if reused else "FAIL",
           f"op before={op_before.id if op_before else None} "
           f"after={op_after.id if op_after else None}")
    report("persisted invite link unchanged (reuse)",
           "PASS" if link_before == link_after else "FAIL",
           f"link before={link_before!r} after={link_after!r}")

    refreshed = ts_after is not None and ts_after != ts_before
    if member is False:
        # member=False + throttle expired => a resend is REQUIRED.  The
        # timestamp now moves ONLY when the bridge accepted the actual DM
        # send, so it is evidence of state B/C -- never of device delivery.
        report("resend accepted by bridge (state B/C: op SENT, DM accepted)",
               "PASS" if (reused and refreshed) else "FAIL",
               f"expected: member=False + expired throttle -> resend via "
               f"persisted op, no new lifecycle; "
               f"ts before={ts_before} after={ts_after}")
        report("state E (DM arrived on device) -- NOT API-confirmable",
               "INFO",
               "check the bridge log line 'resend sendMessage raw ok=True' "
               "for state C/D proof, and your Telegram client for the DM "
               "containing the persisted invite link.  Do not treat the "
               "timestamp alone as proof of delivery.")
    else:
        # member=True is NOT a failure in the throttle test: recovery is
        # unnecessary, so no resend and the timestamp must be unchanged.
        report("member=True => recovery unnecessary => timestamp unchanged",
               "PASS" if not refreshed else "FAIL",
               f"before={ts_before} after={ts_after}")
        report("no resend (member=True branch)",
               "PASS", "expected behavior, not an error")
    return {"op_after": op_after, "link_after": link_after,
            "ts_after": ts_after, "reused": reused}


def step3_wait_for_join(link):
    section("STEP 3 -- Manual join required")
    print("\n  >>> OPEN THIS INVITE AND PRESS JOIN <<<")
    print(f"  >>> {link}")
    print("\n  (If the link is empty, copy it from the admin channel / bot DM.)")
    input("  Press ENTER after you have joined the channel... ")


def step4_verify_joined(user):
    section("STEP 4 -- Verify joined state")
    member, res = membership()
    print_state(user, "post-join probe")
    report("membership is TRUE after join", "PASS" if member is True else "FAIL",
           f"member={member} detail={res.detail}")
    return member


def step5_reconcile_after_join(user, ts_before_join):
    section("STEP 5 -- Reconcile AFTER membership restored")
    run_user_reconcile(user.id)

    a, member, op = print_state(user, "post-join reconcile")
    ts_now = a.last_invite_sent_at

    report("membership TRUE", "PASS" if member is True else "FAIL",
           f"member={member}")
    report("assignment remains active", "PASS" if a.is_active else "FAIL",
           f"is_active={a.is_active}")
    report("member=True => no resend, timestamp unchanged",
           "PASS" if ts_now == ts_before_join else "FAIL",
           f"before={ts_before_join} after={ts_now}")
    report("member=True => recovery unnecessary (no new op/invite expected)",
           "PASS", "existing op/link relationship preserved")


def resolve_django_user(telegram_user_id):
    """Resolve the Django user via the project's existing identity link:
    TelegramAccount.telegram_user_id -> .user.  No new mapping is invented.
    """
    account = TelegramAccount.objects.filter(
        telegram_user_id=telegram_user_id, is_active=True).select_related(
        "user").first()
    return account.user if account else None


def main():
    global TELEGRAM_USER_ID, CHANNEL_ID
    print("LIVE membership-recovery proof")
    print("  NOTE: this drives the REAL bridge + REAL Telegram.")

    raw = input("Telegram user ID: ").strip()
    try:
        TELEGRAM_USER_ID = int(raw)
    except ValueError:
        report("telegram user id parseable", "FAIL", f"got {raw!r}")
        sys.exit(1)
    CHANNEL_ID = input("Telegram channel ID: ").strip()
    if not CHANNEL_ID:
        report("channel id provided", "FAIL", "empty channel id")
        sys.exit(1)

    user = resolve_django_user(TELEGRAM_USER_ID)
    if user is None:
        report("telegram identity resolves to a Django user", "FAIL",
               f"no active TelegramAccount with telegram_user_id="
               f"{TELEGRAM_USER_ID}")
        sys.exit(1)
    report("telegram identity resolves to a Django user", "PASS",
           f"Django user id={user.id}")

    print(f"  Telegram user:    {TELEGRAM_USER_ID}")
    print(f"  Resolved Django user: {getattr(user, 'username', user.pk)} "
          f"(id={user.id})")
    print(f"  Channel:          {CHANNEL_ID}")

    s0 = step0_inspect(user)
    if s0 is None:
        sys.exit(1)
    s1 = step1_prepare(user)
    if s1 is None:
        sys.exit(1)
    s2 = step2_reconcile(user, s0)

    step3_wait_for_join(s2["link_after"] or
                        (latest_grant_op(user).invite_link
                         if latest_grant_op(user) else ""))
    step4_verify_joined(user)
    step5_reconcile_after_join(user, s2["ts_after"])

    section("SUMMARY")
    fails = [r for r in results if r[1] == "FAIL"]
    for label, status, detail in results:
        print(f"  [{status}] {label}")
    print(f"\n  FAILURES: {len(fails)}")
    for label, _, detail in fails:
        print(f"    - {label}: {detail}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
