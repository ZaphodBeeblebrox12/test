#!/usr/bin/env python
"""
test.py -- LIVE lifecycle test: grant / revoke / reactivate / recovery.
=========================================================================
Interactive end-to-end proof against the RUNNING local services
(Django :8000, bot bridge :8010, REAL Telegram).

YOU enter: Telegram user ID + Telegram channel ID.
The Django user is resolved internally via the existing TelegramAccount
identity link.  Nothing is hard-coded.

The script drives the REAL lifecycle:
  GRANT    (entitlement on  -> reconcile -> assignment active, op SENT/COMPLETED)
  REVOKE   (entitlement off -> reconcile -> verified removal, assignment inactive)
  REACTIVATE (entitlement on -> reconcile -> NEW grant lifecycle, unban path)
  RECOVERY (member=False + expired 6h throttle -> resend persisted invite)
  JOINED   (member=True -> no resend, no new lifecycle)

SAFE TO RE-ENTER: the original Subscription state is restored at the end
(pass --no-restore to leave it as the script left it).

Run from the Django project root:

    python test.py
    python test.py --no-restore
"""
from __future__ import annotations

import os
import sys
import argparse
import datetime

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
    BotAccessAudit, PlanChannelMapping, TelegramAccount,
    UserChannelAssignment)
from apps.bot_integration.reconcile_jobs import run_user_reconcile
from apps.bot_integration.services.provision_client import ProvisionClient
from apps.jobs.models import ProvisioningOperation
from apps.subscriptions.models import Subscription

TELEGRAM_USER_ID = None
CHANNEL_ID = None
results = []


# --------------------------------------------------------------------------
# reporting helpers
# --------------------------------------------------------------------------
def report(label, status, detail=""):
    assert status in ("PASS", "FAIL", "INFO")
    results.append((label, status, detail))
    line = f"[{status:4}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# --------------------------------------------------------------------------
# domain helpers (read-only unless stated)
# --------------------------------------------------------------------------
def resolve_django_user(telegram_user_id):
    account = TelegramAccount.objects.filter(
        telegram_user_id=telegram_user_id, is_active=True,
    ).select_related("user").first()
    return account.user if account else None


def get_assignment(user):
    return UserChannelAssignment.objects.filter(
        user=user, platform="telegram", external_id=CHANNEL_ID).first()


def latest_op(user, operation):
    return (ProvisioningOperation.objects
            .filter(user_id=user.id, channel_id=CHANNEL_ID,
                    operation=operation)
            .order_by("-created_at").first())


def membership():
    """Returns (member: True/False/None, raw_status_or_None, ProvisionResult)."""
    res = ProvisionClient().check_membership(TELEGRAM_USER_ID, CHANNEL_ID)
    if res.ok:
        return bool((res.detail or {}).get("member")), \
            (res.detail or {}).get("member_status"), res
    return None, None, res


def active_subscription(user):
    return (Subscription.objects
            .filter(user_id=user.id, is_active=True, status="active")
            .order_by("-created_at").first())


def set_entitlement(user, on):
    """Toggle the user's entitlement.  Returns the affected Subscription."""
    sub = (Subscription.objects
           .filter(user_id=user.id)
           .order_by("-is_active", "-created_at").first())
    if sub is None:
        return None
    Subscription.objects.filter(pk=sub.pk).update(
        is_active=on, status="active" if on else "canceled")
    return sub


def print_state(user, note=""):
    a = get_assignment(user)
    member, status, _res = membership()
    g = latest_op(user, ProvisioningOperation.OP_GRANT)
    r = latest_op(user, ProvisioningOperation.OP_REVOKE)
    if note:
        print(f"  ({note})")
    print(f"  assignment.is_active         = {a.is_active if a else 'NO ROW'}")
    print(f"  assignment.revoked_at        = {a.revoked_at if a else '-'}")
    print(f"  assignment.last_invite_sent_at = {a.last_invite_sent_at if a else '-'}")
    print(f"  membership                   = {member}  (raw status={status!r})")
    print(f"  grant op                     = "
          f"{'id=%s state=%s' % (g.id, g.state) if g else 'none'}  "
          f"link={g.invite_link!r}" if g else "  grant op                     = none")
    print(f"  revoke op                    = "
          f"{'id=%s state=%s' % (r.id, r.state) if r else 'none'}")
    return a, member, status


# --------------------------------------------------------------------------
# lifecycle phases
# --------------------------------------------------------------------------
def phase_grant(user):
    section("PHASE GRANT -- entitlement ON, reconcile")
    sub = set_entitlement(user, True)
    if sub is None:
        report("subscription exists to toggle", "FAIL",
               "no Subscription row for this user; create entitlement first")
        return None
    report("entitlement ON", "INFO", f"subscription id={sub.pk}")

    ts_before = None
    a = get_assignment(user)
    if a:
        ts_before = a.last_invite_sent_at

    run_user_reconcile(user.id)

    a, member, status = print_state(user, "after grant reconcile")
    g = latest_op(user, ProvisioningOperation.OP_GRANT)

    report("assignment active after grant",
           "PASS" if (a and a.is_active) else "FAIL",
           f"is_active={a.is_active if a else 'no row'}")
    report("grant op reached SENT or COMPLETED",
           "PASS" if (g and g.state in (
               ProvisioningOperation.ST_SENT,
               ProvisioningOperation.ST_COMPLETED)) else "FAIL",
           f"state={g.state if g else 'no op'}")
    report("revoked_at cleared on active assignment",
           "PASS" if (a and a.is_active and a.revoked_at is None) else "FAIL",
           f"revoked_at={a.revoked_at if a else '-'}")
    if member is False:
        report("invite sent -> last_invite_sent_at stamped",
               "PASS" if (a and a.last_invite_sent_at) else "FAIL",
               f"ts={a.last_invite_sent_at if a else '-'}")
    else:
        # member=True is the CORRECT "already a member, no recovery needed"
        # branch: no resend, timestamp must NOT move.  Not a failure.
        report("member=True => already a member, no resend needed (EXPECTED)",
               "PASS",
               "timestamp unchanged is the correct outcome here")
        report("timestamp unchanged (member=True branch)",
               "PASS" if (a and a.last_invite_sent_at == ts_before) else "FAIL",
               f"before={ts_before} after={a.last_invite_sent_at if a else '-'}")
    return {"member": member, "status": status}


def phase_revoke(user):
    section("PHASE REVOKE -- entitlement OFF, reconcile")
    set_entitlement(user, False)
    report("entitlement OFF", "INFO")

    run_user_reconcile(user.id)

    a, member, status = print_state(user, "after revoke reconcile")
    r = latest_op(user, ProvisioningOperation.OP_REVOKE)

    ok = True
    if not (r and r.state == ProvisioningOperation.ST_COMPLETED):
        report("revoke op COMPLETED (verified removal)", "FAIL",
               f"revoke op state={r.state if r else 'no op'} -- the bridge "
               f"reported success but membership was not verified removed")
        ok = False
    else:
        report("revoke op COMPLETED (verified removal)", "PASS",
               f"op id={r.id}")
    if member is not False:
        report("membership became non-member after revoke", "FAIL",
               f"member={member} raw status={status!r} -- user may still be "
               f"in the channel (see bridge revoke diagnostics)")
        ok = False
    else:
        report("membership became non-member after revoke", "PASS",
               f"raw status={status!r}")
    if not (a and not a.is_active):
        report("assignment inactive after revoke", "FAIL",
               f"is_active={a.is_active if a else 'no row'}")
        ok = False
    else:
        report("assignment inactive after revoke", "PASS",
               f"revoked_at={a.revoked_at}")
    return {"ok": ok, "member": member}


def phase_reactivate(user):
    section("PHASE REACTIVATE -- entitlement ON again (post-revoke grant)")
    g_old = latest_op(user, ProvisioningOperation.OP_GRANT)
    set_entitlement(user, True)
    report("entitlement ON", "INFO")

    run_user_reconcile(user.id)

    a, member, status = print_state(user, "after reactivation reconcile")
    g = latest_op(user, ProvisioningOperation.OP_GRANT)

    report("assignment active after reactivation",
           "PASS" if (a and a.is_active) else "FAIL",
           f"is_active={a.is_active if a else 'no row'}")
    if g_old and g and g.id != g_old.id:
        report("NEW grant lifecycle created (old op not reused)",
               "PASS", f"old={g_old.id} new={g.id}")
    elif g and g_old is None:
        report("NEW grant lifecycle created", "PASS", f"new={g.id}")
    else:
        report("NEW grant lifecycle created (old op not reused)", "FAIL",
               f"grant op id={g.id if g else None} "
               f"(same as before: {g_old.id if g_old else None}) -- a stale "
               f"post-revoke grant op was reused; the unban path was skipped")
    g_old_state = None
    if g_old:
        g_old.refresh_from_db()
        g_old_state = f"{g_old.state}/{g_old.last_error}"
    report("old grant op superseded by revoke", "PASS"
           if (g_old is None or g_old.state == ProvisioningOperation.ST_FAILED)
           else "FAIL",
           f"old op now: {g_old_state}")
    report("op ST_SENT while membership pending (invite sent, not joined)",
           "PASS" if (g and g.state == ProvisioningOperation.ST_SENT
                      and member is False) else "INFO",
           f"op state={g.state if g else '-'} member={member}")
    return {"member": member, "status": status, "grant_op": g}


def phase_recovery(user):
    section("PHASE RECOVERY -- member=False + expired 6h throttle")
    a = get_assignment(user)
    member, status, _ = membership()
    if member is not False:
        report("membership is False (precondition)", "FAIL",
               f"member={member} -- leave the channel to test recovery")
        return None
    g_before = latest_op(user, ProvisioningOperation.OP_GRANT)
    link_before = g_before.invite_link if g_before else None

    a.last_invite_sent_at = timezone.now() - datetime.timedelta(hours=7)
    a.save(update_fields=["last_invite_sent_at"])
    report("throttle forced expired (7h ago)", "INFO")

    run_user_reconcile(user.id)

    a, member2, _ = print_state(user, "after recovery reconcile")
    g_after = latest_op(user, ProvisioningOperation.OP_GRANT)
    link_after = g_after.invite_link if g_after else None

    report("same grant op reused (no duplicate lifecycle)",
           "PASS" if (g_before and g_after
                      and g_before.id == g_after.id) else "FAIL",
           f"before={g_before.id if g_before else None} "
           f"after={g_after.id if g_after else None}")
    report("persisted invite link reused",
           "PASS" if link_before == link_after else "FAIL",
           f"before={link_before!r} after={link_after!r}")
    ts_ok = (a.last_invite_sent_at and
             a.last_invite_sent_at >
             timezone.now() - datetime.timedelta(hours=1))
    report("resend accepted by bridge (timestamp moves only on accepted DM)",
           "PASS" if ts_ok else "FAIL",
           f"after={a.last_invite_sent_at}")
    report("state E (device delivery) not API-confirmable", "INFO",
           "verify bridge log 'resend sendMessage raw ok=True' + Telegram client")
    report("assignment still active", "PASS" if a.is_active else "FAIL")
    return {"invite_link": link_after}


def phase_joined(user, ts_anchor):
    section("PHASE JOINED -- press Join, then verify no-op reconcile")
    input("\n  >>> Open the persisted invite link above, press JOIN, then "
          "press ENTER here... ")
    member, status, _ = membership()
    report("membership TRUE after join", "PASS" if member is True else "FAIL",
           f"member={member} raw status={status!r}")

    g_before = latest_op(user, ProvisioningOperation.OP_GRANT)
    run_user_reconcile(user.id)

    a, member2, _ = print_state(user, "post-join reconcile")
    g_after = latest_op(user, ProvisioningOperation.OP_GRANT)
    report("no new grant op after join",
           "PASS" if (g_before and g_after
                      and g_before.id == g_after.id) else "FAIL")
    report("last_invite_sent_at unchanged (member=True => no resend)",
           "PASS" if (a.last_invite_sent_at == ts_anchor) else "FAIL",
           f"anchor={ts_anchor} after={a.last_invite_sent_at}")
    report("assignment remains active", "PASS" if a.is_active else "FAIL")


# --------------------------------------------------------------------------
def main():
    global TELEGRAM_USER_ID, CHANNEL_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-restore", action="store_true",
                        help="do not restore the original subscription state")
    args = parser.parse_args()

    print("LIVE lifecycle test: grant / revoke / reactivate / recovery")
    print("  (drives REAL bridge + REAL Telegram; entitlement is toggled and")
    print("   restored at the end unless --no-restore)")

    raw = input("Telegram user ID: ").strip()
    try:
        TELEGRAM_USER_ID = int(raw)
    except ValueError:
        report("telegram user id parseable", "FAIL", f"got {raw!r}")
        sys.exit(1)
    CHANNEL_ID = input("Telegram channel ID: ").strip()
    if not CHANNEL_ID:
        report("channel id provided", "FAIL", "empty")
        sys.exit(1)

    user = resolve_django_user(TELEGRAM_USER_ID)
    if user is None:
        report("telegram identity resolves to a Django user", "FAIL",
               f"no active TelegramAccount with telegram_user_id="
               f"{TELEGRAM_USER_ID}")
        sys.exit(1)
    report("telegram identity resolves to a Django user", "PASS",
           f"Django user={getattr(user, 'username', user.pk)} id={user.id}")
    print(f"  Telegram user:        {TELEGRAM_USER_ID}")
    print(f"  Resolved Django user: {getattr(user, 'username', user.pk)}")
    print(f"  Channel:              {CHANNEL_ID}")

    sub0 = (Subscription.objects
            .filter(user_id=user.id)
            .order_by("-is_active", "-created_at").first())
    if sub0 is None:
        report("subscription exists", "FAIL",
               "no Subscription row; cannot drive lifecycle")
        sys.exit(1)
    saved_state = (sub0.pk, sub0.is_active, sub0.status)
    report("subscription captured for restore", "INFO",
           f"id={sub0.pk} is_active={sub0.is_active} status={sub0.status!r}")

    # ---- baseline ----
    section("STEP 1 -- Baseline state (read-only)")
    print_state(user, "baseline")

    # ---- grant ----
    g = phase_grant(user)
    if g is None:
        sys.exit(1)

    # ---- revoke ----
    rv = phase_revoke(user)
    if not rv["ok"]:
        print("\n  Revoke phase failed; continuing to reactivation is unsafe.")
    else:
        # ---- reactivate ----
        ra = phase_reactivate(user)
        if ra["member"] is False:
            rec = phase_recovery(user)
            if rec:
                a = get_assignment(user)
                phase_joined(user, a.last_invite_sent_at)

    # ---- restore ----
    if not args.no_restore:
        Subscription.objects.filter(pk=saved_state[0]).update(
            is_active=saved_state[1], status=saved_state[2])
        report("subscription state restored", "INFO",
               f"id={saved_state[0]} is_active={saved_state[1]} "
               f"status={saved_state[2]!r}")
        run_user_reconcile(user.id)
        report("post-restore reconcile ran (converges to original state)",
               "INFO")
    else:
        report("subscription state NOT restored (--no-restore)", "INFO")

    section("SUMMARY")
    fails = [x for x in results if x[1] == "FAIL"]
    for label, status, detail in results:
        print(f"  [{status:4}] {label}" + (f" -- {detail}" if detail else ""))
    print(f"\n  FAILURES: {len(fails)}")
    for label, _, detail in fails:
        print(f"    - {label}: {detail}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
