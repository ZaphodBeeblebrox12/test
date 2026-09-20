"""Reconcile bridge tests (Django-native job system).

The reconcile body is run_user_reconcile (apps.bot_integration.reconcile_jobs):
diff -> ProvisioningOperation state machine -> UserChannelAssignment finalization.
ProvisionTransport is mocked (no network, no real Telegram calls).
Discord behavior is unchanged (direct service)."""
from unittest import mock
from django.test import TestCase

from apps.accounts.models import User
from apps.bot_integration.models import (
    TelegramAccount, DiscordAccount, PlanChannelMapping,
    UserChannelAssignment, BotAccessAudit)
from apps.bot_integration.reconcile_jobs import run_user_reconcile
from apps.jobs.models import ProvisioningOperation
from apps.subscriptions.models import Plan, Subscription


def make_user(u):
    return User.objects.create(username=u, email=f"{u}@x.com")


class FakeTransport:
    """Happy-path transport: grant/revoke succeed immediately."""

    def __init__(self):
        self.grants = []    # (tg_id, channel_id)
        self.revokes = []
        self.member = True  # in the channel until a successful revoke

    def create_invite_link(self, telegram_user_id, channel_id,
                           idempotency_key=None):
        self.grants.append((telegram_user_id, channel_id, idempotency_key))
        return "https://t.me/+fake"

    def send_invite_dm(self, telegram_user_id, channel_id, invite_link):
        return True

    def is_member(self, telegram_user_id, channel_id):
        return self.member  # revoke() flips it to False on a successful ban

    def revoke(self, telegram_user_id, channel_id, idempotency_key=None):
        self.revokes.append((telegram_user_id, channel_id, idempotency_key))
        self.member = False  # successful ban removes the user
        return mock.Mock(ok=True, retryable=False)


def run(uid, transport):
    run_user_reconcile(uid, transport=transport)


class TelegramReconcileBridgeTests(TestCase):
    """Each (user, channel, intent) gets ONE open ProvisioningOperation;
    a new lifecycle after completion gets a NEW operation row."""

    def setUp(self):
        self.user = make_user("tg1")
        self.plan = Plan.objects.create(name="Pro", display_order=1)
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001", name="Pro Chat")
        self.account = TelegramAccount.objects.create(
            user=self.user, telegram_user_id=555, chat_id=555, is_active=True)

    def test_grant_creates_operation_and_assignment(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        op = ProvisioningOperation.objects.get(user_id=self.user.id,
                                               channel_id="-1001",
                                               operation="grant")
        self.assertIn((555, "-1001", op.provision_key), t.grants)
        self.assertEqual(op.state, ProvisioningOperation.ST_COMPLETED)
        self.assertEqual(op.invite_link, "https://t.me/+fake")
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, external_id="-1001", is_active=True).exists())
        self.assertEqual(BotAccessAudit.objects.filter(
            status="success", action="grant").count(), 1)

    def test_second_run_is_noop_no_new_operation(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        audits = BotAccessAudit.objects.count()
        ops = ProvisioningOperation.objects.count()
        run(self.user.id, t)   # duplicate trigger: no new operation, no new grant
        self.assertEqual(ProvisioningOperation.objects.count(), ops)
        self.assertEqual(len(t.grants), 1)
        # finalize steps are idempotent (assignment get_or_create; audit may repeat
        # only when a NEW operation runs -- none did here)
        self.assertEqual(BotAccessAudit.objects.count(), audits)

    def test_plan_change_revokes_old_channel(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        plan2 = Plan.objects.create(name="Elite", tier="pro", display_order=2)
        PlanChannelMapping.objects.create(plan=plan2, platform="telegram",
                                          external_id="-1002")
        sub = Subscription.objects.get(user=self.user)
        sub.plan = plan2
        sub.save()
        run(self.user.id, t)
        self.assertEqual([(r[0], r[1]) for r in t.revokes], [(555, "-1001")])
        self.assertFalse(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())

    def test_lapse_revokes_everything(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        sub = Subscription.objects.get(user=self.user)
        sub.status = "expired"
        sub.is_active = False
        sub.save()
        run(self.user.id, t)
        self.assertEqual([(r[0], r[1]) for r in t.revokes], [(555, "-1001")])
        self.assertFalse(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())

    def test_grant_revoke_grant_new_lifecycle_new_operation(self):
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        first_op = ProvisioningOperation.objects.get(operation="grant")
        # revoke
        sub.status = "expired"; sub.is_active = False; sub.save()
        run(self.user.id, t)
        # re-grant
        sub.status = "active"; sub.is_active = True; sub.save()
        run(self.user.id, t)
        grants = ProvisioningOperation.objects.filter(operation="grant")
        self.assertEqual(grants.count(), 2)
        self.assertNotEqual(grants.order_by("id")[0].operation_id,
                            grants.order_by("id")[1].operation_id)
        self.assertTrue(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())


class DiscordReconcileTests(TestCase):
    """Discord transport unchanged — direct service calls."""

    def setUp(self):
        self.user = make_user("dc1")
        self.plan = Plan.objects.create(name="Pro", display_order=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="discord",
                                          external_id="111")
        self.account = DiscordAccount.objects.create(
            user=self.user, discord_user_id="9", roles=[], is_active=True)

    @mock.patch("apps.bot_integration.reconcile.DiscordBotService")
    def test_lapse_removes_roles(self, svc):
        from apps.bot_integration.reconcile import reconcile_user_access
        svc.add_role.return_value = True
        svc.remove_role.return_value = True
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          status="active", is_active=True)
        reconcile_user_access(self.user.id)
        self.account.refresh_from_db()
        self.assertEqual(self.account.roles, ["111"])
        sub.status = "expired"; sub.is_active = False; sub.save()
        reconcile_user_access(self.user.id)
        svc.remove_role.assert_called_once_with("9", "111")
        self.account.refresh_from_db()
        self.assertEqual(self.account.roles, [])


# ---------------------------------------------------------------------------
# Membership recovery: entitled + assigned but no longer a channel member.
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402  (used by recovery tests)
from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402


class MembershipRecoveryTransport(FakeTransport):
    """Configurable membership answers; records DMs and member probes."""

    def __init__(self, member):
        super().__init__()
        self.member = member
        self.dms = []
        self.member_checks = 0

    def send_invite_dm(self, telegram_user_id, channel_id, invite_link):
        self.dms.append((telegram_user_id, channel_id, invite_link))
        return True

    def is_member(self, telegram_user_id, channel_id):
        self.member_checks += 1
        return self.member


class MembershipRecoveryTests(TestCase):
    def setUp(self):
        self.user = make_user("recover")
        self.plan = Plan.objects.create(name="Pro", display_order=2)
        self.mapping = PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001",
            name="Pro Chat")
        self.account = TelegramAccount.objects.create(
            user=self.user, telegram_user_id=555, chat_id=555, is_active=True)
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)

    def _run(self, transport):
        run_user_reconcile(self.user.id, transport=transport)

    def _assignment(self):
        return UserChannelAssignment.objects.get(
            user=self.user, platform="telegram", external_id="-1001")

    def test_entitled_assigned_member_no_invite(self):
        self._run(MembershipRecoveryTransport(member=True))
        assert self._assignment().is_active
        audits_before = BotAccessAudit.objects.count()
        t = MembershipRecoveryTransport(member=True)
        self._run(t)
        self.assertEqual(t.grants, [])            # no new invite minted
        self.assertEqual(t.dms, [])               # no DM sent
        self.assertGreaterEqual(t.member_checks, 1)  # membership was checked
        self.assertEqual(BotAccessAudit.objects.count(), audits_before)
        self.assertEqual(
            ProvisioningOperation.objects.filter(
                user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT
            ).count(), 1)

    def test_not_member_recovers_invite_throttles_then_resends(self):
        # Run 1: normal grant completes (member at grant time).
        self._run(MembershipRecoveryTransport(member=True))
        assert self._assignment().is_active
        assert self._assignment().last_invite_sent_at is None  # nothing resent

        # Run 2: user lost -> recovery creates a NEW operation + invite
        # (old one is completed, so nothing reusable), sends it, stamps throttle.
        t2 = MembershipRecoveryTransport(member=False)
        self._run(t2)
        a = self._assignment()
        self.assertTrue(a.is_active)              # entitlement persists
        self.assertEqual(len(t2.grants), 1)       # new invite minted
        # initial delivery is SERVER-SIDE inside do_grant; no local resend DM
        self.assertEqual(len(t2.dms), 0)
        self.assertIsNotNone(a.last_invite_sent_at)
        first_link = "https://t.me/+fake"  # FakeTransport.create_invite_link return value
        op2 = (ProvisioningOperation.objects
               .filter(user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
               .order_by("id").last())
        self.assertEqual(op2.invite_link, first_link)
        self.assertEqual(
            ProvisioningOperation.objects.filter(
                user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT
            ).count(), 2)                          # new lifecycle created
        audits_before = BotAccessAudit.objects.count()

        # Run 3: inside the 6h throttle -> NO resend, no new probe effects.
        t3 = MembershipRecoveryTransport(member=False)
        self._run(t3)
        self.assertEqual(len(t3.grants), 0)       # no new invite
        self.assertEqual(len(t3.dms), 0)          # no resend
        # throttle short-circuits BEFORE the membership probe too
        self.assertEqual(t3.member_checks, 0)
        self.assertEqual(BotAccessAudit.objects.count(), audits_before)

        # Run 4: after throttle expiry -> persisted invite link REUSED
        # (no new create_invite_link), DM resent, throttle refreshed.
        a.last_invite_sent_at = timezone.now() - timedelta(hours=7)
        a.save(update_fields=["last_invite_sent_at"])
        t4 = MembershipRecoveryTransport(member=False)
        self._run(t4)
        self.assertEqual(len(t4.grants), 0)       # persisted link reused
        self.assertEqual(len(t4.dms), 1)          # link re-sent
        self.assertEqual(t4.dms[0][2], first_link)  # SAME persisted link
        self.assertLess(
            timezone.now() - self._assignment().last_invite_sent_at,
            timedelta(minutes=1))  # throttle was refreshed to "now"
        self.assertTrue(self._assignment().is_active)

    def test_membership_error_never_treated_as_not_member(self):
        self._run(MembershipRecoveryTransport(member=True))
        t = MembershipRecoveryTransport(member=None)  # undetermined/retryable
        self._run(t)
        self.assertEqual(t.grants, [])
        self.assertEqual(t.dms, [])
        self.assertIsNone(self._assignment().last_invite_sent_at)
        self.assertEqual(
            ProvisioningOperation.objects.filter(
                user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT
            ).count(), 1)

    def test_revoke_then_regrant_new_lifecycle_after_recovery(self):
        # Grant completes; user lost; recovery sends; THEN entitlement is
        # revoked and re-granted -> brand new operation lifecycle allowed.
        self._run(MembershipRecoveryTransport(member=True))
        self._run(MembershipRecoveryTransport(member=False))
        assert self._assignment().last_invite_sent_at is not None
        self._run(MembershipRecoveryTransport(member=None))  # no-op, error
        Subscription.objects.filter(user=self.user).update(is_active=False)
        self._run(MembershipRecoveryTransport(member=False))
        self.assertFalse(self._assignment().is_active)
        self.assertEqual(
            ProvisioningOperation.objects.filter(
                user_id=self.user.id, operation=ProvisioningOperation.OP_REVOKE
            ).count(), 1)
        # The completed revoke must have superseded the ST_SENT recovery op.
        op2 = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT,
            state=ProvisioningOperation.ST_FAILED,
            last_error="superseded_by_revoke")
        self.assertEqual(op2.state, ProvisioningOperation.ST_FAILED)
        self.assertEqual(op2.last_error, "superseded_by_revoke")

        Subscription.objects.filter(user=self.user).update(is_active=True)
        t = MembershipRecoveryTransport(member=True)
        self._run(t)
        self.assertTrue(self._assignment().is_active)
        # A NEW grant lifecycle is created (never reusing the superseded op);
        # the full grant path runs (create_invite_link -> client.grant ->
        # bot do_grant -> unban) even though the user is already a member.
        self.assertEqual(len(t.grants), 1)
        self.assertEqual(
            ProvisioningOperation.objects.filter(
                user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT
            ).count(), 3)  # original + recovery + fresh post-revoke grant
        op3 = (ProvisioningOperation.objects
               .filter(user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
               .order_by("id").last())
        self.assertEqual(op3.state, ProvisioningOperation.ST_COMPLETED)

    def test_live_case_invite_sent_pending_membership_activates_assignment(self):
        """Exact live lifecycle: grant delivers invite while member=False."""
        # 1-4: entitlement active, no active assignment yet, grant runs with
        # membership False (user has not joined).
        t1 = MembershipRecoveryTransport(member=False)
        self._run(t1)
        # 5: assignment becomes active even though membership is False.
        a = self._assignment()
        self.assertTrue(a.is_active)
        # 6: operation remains ST_SENT while waiting for membership.
        op = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(op.state, ProvisioningOperation.ST_SENT)
        self.assertEqual(op.invite_link, "https://t.me/+fake")
        # 7: last_invite_sent_at populated at send time.
        self.assertIsNotNone(a.last_invite_sent_at)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="grant", status="success").count(), 1)

        # 8: next reconcile, member=False INSIDE 6h -> no resend at all.
        t2 = MembershipRecoveryTransport(member=False)
        self._run(t2)
        self.assertEqual(t2.grants, [])
        self.assertEqual(t2.dms, [])
        self.assertEqual(t2.member_checks, 0)  # throttle short-circuits first
        self.assertEqual(ProvisioningOperation.objects.filter(
            user_id=self.user.id,
            operation=ProvisioningOperation.OP_GRANT).count(), 1)

        # 9: after 6h -> SAME persisted invite re-sent, no new invite minted.
        a.last_invite_sent_at = timezone.now() - timedelta(hours=7)
        a.save(update_fields=["last_invite_sent_at"])
        t3 = MembershipRecoveryTransport(member=False)
        self._run(t3)
        self.assertEqual(t3.grants, [])
        self.assertEqual(len(t3.dms), 1)
        self.assertEqual(t3.dms[0][2], "https://t.me/+fake")
        self.assertEqual(ProvisioningOperation.objects.filter(
            user_id=self.user.id,
            operation=ProvisioningOperation.OP_GRANT).count(), 1)

        # 10: user joins -> member=True -> no further invite ever.
        t4 = MembershipRecoveryTransport(member=True)
        self._run(t4)
        self.assertEqual(t4.grants, [])
        self.assertEqual(t4.dms, [])
        self.assertTrue(self._assignment().is_active)

    def test_failed_send_keeps_operation_state_and_retries_honestly(self):
        """A failed RECOVERY resend must keep the op FAILED with its error,
        leave the assignment active, release the throttle claim, and audit
        the failure -- then a later reconcile retries honestly."""
        class FailingResendTransport(MembershipRecoveryTransport):
            def send_invite_dm(self, telegram_user_id, channel_id,
                               invite_link):
                self.dms.append((telegram_user_id, channel_id, invite_link))
                return False

        t1 = MembershipRecoveryTransport(member=False)
        self._run(t1)  # grant mints; server-side DM; op SENT (member False)
        op1 = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(op1.state, ProvisioningOperation.ST_SENT)
        a = self._assignment()
        expired = timezone.now() - timedelta(hours=7)
        a.last_invite_sent_at = expired
        a.save(update_fields=["last_invite_sent_at"])

        # recovery resend FAILS at the local DM step
        tf = FailingResendTransport(member=False)
        self._run(tf)
        op1.refresh_from_db()
        self.assertEqual(op1.state, ProvisioningOperation.ST_FAILED)
        self.assertIsNotNone(op1.last_error)
        a.refresh_from_db()
        # claim released: no send happened, window not consumed
        self.assertEqual(a.last_invite_sent_at, expired)
        self.assertTrue(a.is_active)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="grant",
            status="success").count(), 1)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="grant",
            status="failed").count(), 1)

        # later reconcile retries: the FAILED op is terminal, so a fresh
        # grant lifecycle is minted and delivered (server-side DM; recovery
        # audits only on COMPLETED/FAILED, so the success count is unchanged)
        t2 = MembershipRecoveryTransport(member=False)
        self._run(t2)
        a.refresh_from_db()
        self.assertGreater(a.last_invite_sent_at, expired)
        self.assertTrue(a.is_active)
        ops = ProvisioningOperation.objects.filter(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(ops.count(), 2)
        self.assertEqual(ops.order_by("id").last().state,
                         ProvisioningOperation.ST_SENT)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="grant",
            status="success").count(), 1)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="grant",
            status="failed").count(), 1)

    def send_invite_dm(self, telegram_user_id, channel_id,
                               invite_link):
                self.dms.append((telegram_user_id, channel_id, invite_link))
                return False

        # Send fails: operation must be ST_FAILED, assignment NOT activated,
        # nothing recorded as sent.
    def test_sent_grant_after_completed_revoke_reruns_full_grant_path(self):
        """Exact production failure: ST_SENT grant + revoke + re-grant must
        NOT reuse the old grant op (which would skip the bridge do_grant and
        its unban).  Steps 1-10 from the live incident."""
        # 1: grant runs, user not yet a member -> op ST_SENT, assignment active
        t1 = MembershipRecoveryTransport(member=False)
        self._run(t1)
        op1 = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(op1.state, ProvisioningOperation.ST_SENT)
        self.assertTrue(self._assignment().is_active)
        key1 = op1.provision_key

        # 2: completed revoke
        Subscription.objects.filter(user=self.user).update(is_active=False)
        t2 = MembershipRecoveryTransport(member=False)
        self._run(t2)
        self.assertFalse(self._assignment().is_active)
        self.assertEqual(
            ProvisioningOperation.objects.filter(
                user_id=self.user.id,
                operation=ProvisioningOperation.OP_REVOKE,
                state=ProvisioningOperation.ST_COMPLETED).count(), 1)
        op1.refresh_from_db()
        self.assertEqual(op1.state, ProvisioningOperation.ST_FAILED)
        self.assertEqual(op1.last_error, "superseded_by_revoke")

        # 3: subscription active again
        Subscription.objects.filter(user=self.user).update(is_active=True)

        # 4-7: NEW grant op + full grant path (create_invite_link ->
        # ProvisionClient.grant -> /provision/v1/access -> bot do_grant ->
        # unban), with a fresh per-lifecycle idempotency key.
        t3 = MembershipRecoveryTransport(member=False)
        self._run(t3)
        grants = ProvisioningOperation.objects.filter(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(grants.count(), 2)
        op2 = grants.order_by("id").last()
        self.assertNotEqual(op2.id, op1.id)
        self.assertEqual(len(t3.grants), 1)
        self.assertEqual(t3.grants[0][0], 555)
        self.assertEqual(t3.grants[0][1], "-1001")
        self.assertTrue(t3.grants[0][2])
        self.assertNotEqual(t3.grants[0][2], "grant:555:-1001")
        self.assertNotEqual(t3.grants[0][2], key1)

        # 8-10: assignment active, op ST_SENT while membership still False
        self.assertTrue(self._assignment().is_active)
        self.assertEqual(op2.state, ProvisioningOperation.ST_SENT)

    def test_sent_grant_without_revoke_still_reusable_for_recovery(self):
        """No intervening revoke -> open ST_SENT grant stays reusable for
        recovery; persisted link resent, no new op, no new invite."""
        t1 = MembershipRecoveryTransport(member=False)
        self._run(t1)
        op1 = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(op1.state, ProvisioningOperation.ST_SENT)
        a = self._assignment()
        a.last_invite_sent_at = timezone.now() - timedelta(hours=7)
        a.save(update_fields=["last_invite_sent_at"])
        t2 = MembershipRecoveryTransport(member=False)
        self._run(t2)
        self.assertEqual(len(t2.grants), 0)
        self.assertEqual(len(t2.dms), 1)
        self.assertEqual(t2.dms[0][2], "https://t.me/+fake")
        self.assertEqual(ProvisioningOperation.objects.filter(
            user_id=self.user.id,
            operation=ProvisioningOperation.OP_GRANT).count(), 1)
        op1.refresh_from_db()
        self.assertEqual(op1.state, ProvisioningOperation.ST_SENT)

    def test_revoke_ok_but_user_still_member_is_not_falsely_completed(self):
        """Live incident: bot/provisioning reported revoke success but the
        user remained a channel member.  Completion MUST require verified
        removal, and a failed verification must leave the assignment ACTIVE
        and be retried with a fresh operation."""
        class RevokeNoEffectTransport(MembershipRecoveryTransport):
            def revoke(self, telegram_user_id, channel_id,
                       idempotency_key=None):
                self.revokes.append((telegram_user_id, channel_id,
                                     idempotency_key))
                return mock.Mock(ok=True, retryable=False)  # says OK, no effect

        t0 = MembershipRecoveryTransport(member=True)  # establish the grant first
        self._run(t0)
        Subscription.objects.filter(user=self.user).update(is_active=False)
        t1 = RevokeNoEffectTransport(member=True)  # still a member
        self._run(t1)
        op1 = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_REVOKE)
        self.assertEqual(op1.state, ProvisioningOperation.ST_FAILED)
        self.assertIn("still a member", op1.last_error)
        # NOT finalized: assignment stays active, no success audit
        self.assertTrue(self._assignment().is_active)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="revoke",
            status="success").count(), 0)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="revoke",
            status="failed").count(), 1)

        # Next reconcile retries with a FRESH revoke operation.
        t2 = RevokeNoEffectTransport(member=True)
        self._run(t2)
        self.assertEqual(len(t2.revokes), 1)
        self.assertEqual(ProvisioningOperation.objects.filter(
            user_id=self.user.id,
            operation=ProvisioningOperation.OP_REVOKE).count(), 2)

    def test_revoke_forwards_per_operation_idempotency_key(self):
        t = MembershipRecoveryTransport(member=True)
        self._run(t)
        Subscription.objects.filter(user=self.user).update(is_active=False)
        self._run(t)
        op = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_REVOKE)
        self.assertEqual(len(t.revokes), 1)
        self.assertEqual(t.revokes[0][2], op.provision_key)
        self.assertTrue(op.provision_key.startswith("revoke:"))

    def test_grant_reactivation_clears_revoked_at(self):
        t = MembershipRecoveryTransport(member=True)
        self._run(t)
        Subscription.objects.filter(user=self.user).update(is_active=False)
        self._run(t)  # revoke flips membership to non-member -> completes
        a = self._assignment()
        self.assertFalse(a.is_active)
        self.assertIsNotNone(a.revoked_at)
        t.member = True  # user re-joins / is present for the re-grant
        Subscription.objects.filter(user=self.user).update(is_active=True)
        self._run(t)
        a.refresh_from_db()
        self.assertTrue(a.is_active)
        self.assertIsNone(a.revoked_at)

    def test_revoke_success_verified_removal_completes(self):
        """Successful Telegram ban -> membership becomes non-member ->
        revoke completes, assignment inactive with revoked_at stamped."""
        t = MembershipRecoveryTransport(member=True)
        self._run(t)                       # grant (member at that time)
        Subscription.objects.filter(user=self.user).update(is_active=False)
        self._run(t)                       # revoke; t.revoke() flips membership
        op = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_REVOKE)
        self.assertEqual(op.state, ProvisioningOperation.ST_COMPLETED)
        a = self._assignment()
        self.assertFalse(a.is_active)
        self.assertIsNotNone(a.revoked_at)
        self.assertEqual(BotAccessAudit.objects.filter(
            user_id=self.user.id, action="revoke",
            status="success").count(), 1)

    def test_failed_dm_releases_throttle_claim_and_later_retry_succeeds(self):
        """The atomic claim must be released when no invite actually went
        out, so a later reconcile retries.  The timestamp only ever reflects
        an actual successful send."""
        class FailingResendTransport(MembershipRecoveryTransport):
            def send_invite_dm(self, telegram_user_id, channel_id,
                               invite_link):
                self.dms.append((telegram_user_id, channel_id, invite_link))
                return False  # DM delivery failed

        t1 = MembershipRecoveryTransport(member=False)
        self._run(t1)  # grant completes -> op SENT
        a = self._assignment()
        expired = timezone.now() - timedelta(hours=7)
        a.last_invite_sent_at = expired
        a.save(update_fields=["last_invite_sent_at"])

        # recovery attempt: claim won, probe False, DM FAILED
        tf = FailingResendTransport(member=False)
        self._run(tf)
        a.refresh_from_db()
        self.assertEqual(len(tf.dms), 1)
        # claim RELEASED: timestamp restored to the expired value
        self.assertEqual(a.last_invite_sent_at, expired)
        op1 = ProvisioningOperation.objects.get(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(op1.state, ProvisioningOperation.ST_FAILED)
        self.assertTrue(a.is_active)  # entitlement unaffected

        # later reconcile retries: new op (FAILED is terminal), send works,
        # timestamp moves -- proving the failure did not consume the window.
        t2 = MembershipRecoveryTransport(member=False)
        self._run(t2)
        a.refresh_from_db()
        self.assertGreater(a.last_invite_sent_at, expired)
        self.assertEqual(len(t2.dms), 0)  # grant path DMs server-side
        ops = ProvisioningOperation.objects.filter(
            user_id=self.user.id, operation=ProvisioningOperation.OP_GRANT)
        self.assertEqual(ops.count(), 2)
        op2 = ops.order_by("id").last()
        self.assertEqual(op2.state, ProvisioningOperation.ST_SENT)
        self.assertTrue(self._assignment().is_active)

    def test_member_true_releases_claim_without_resend(self):
        """member=True: nothing to recover -- the claim is released and no
        resend happens, leaving the timestamp untouched."""
        t1 = MembershipRecoveryTransport(member=True)
        self._run(t1)  # member at grant time -> op COMPLETED
        a = self._assignment()
        expired = timezone.now() - timedelta(hours=7)
        a.last_invite_sent_at = expired
        a.save(update_fields=["last_invite_sent_at"])

        t2 = MembershipRecoveryTransport(member=True)
        self._run(t2)
        a.refresh_from_db()
        self.assertEqual(a.last_invite_sent_at, expired)  # released, unchanged
        self.assertEqual(len(t2.dms), 0)
        self.assertEqual(len(t2.grants), 0)
