# Community Platform — Architecture Refactor (pre-production)

Drop-in restructure. No framework change, no migrations (no model changed).

## 1. Growth split (mechanical, hand-verified)
apps/growth/services.py (1508 lines) -> apps/growth/services/ package:
  rewards.py    UserRewardBalance, ReferralRewardService, SubscriptionCreditService
  referrals.py  ReferralService   (single verified cross-domain import: -> rewards)
  gifts.py      7 gift exceptions + GiftService, LegacyGiftService,
                GiftClaimService, GiftEmailService
Boundaries verified by reading section markers (lines 56/625/958) and the
dependency graph; exactly ONE directional edge (referrals imports 3 rewards
classes). No cycles. All 9 callers updated (accounts, payments, growth
views/admin_views/tasks/adapters/api + mgmt command + dashboard mixin).

## 2. Provisioning engine (apps/bot_integration)
Removed sync.py. New:
  access.py      pure desired-state computation (Subscription + PlanChannelMapping)
  reconcile.py   diff-based orchestrator; idempotent; per-user cache lock;
                 audits every op to BotAccessAudit
  tasks.py       per-user task (retry x3/60s) + periodic sweep that now also
                 covers Discord-only users, recent failures, and orphans
  signals.py     reconciles ONLY on subscription activation/deactivation
                 transitions (old code reconciled on EVERY save of an active sub)
Behavioural fixes (intentional):
  * Discord roles are now REVOKED on lapse (old code silently did nothing).
  * Telegram channels from a PREVIOUS plan are now banned (plan-change drift).
  * No-op reconciles are silent (old re-unbanned + audited all targets each run).
  * TelegramBotService.signatures verified (unban_user/ban_user/create_one_time_
    invite_link/send_message); DiscordBotService (add_role/remove_role).

## Tests added
apps/bot_integration/tests/test_reconcile.py   8 tests (grant, idempotent-noop,
  retry-on-failed-send, plan-change-revoke, lapse; discord lapse-removes-roles)
apps/growth/tests/test_service_imports.py      import smoke test for the split

## Verify after applying
  python manage.py makemigrations --check --dry-run   # expect: no changes
  python manage.py check
  python manage.py test apps.growth apps.bot_integration

## Removed junk (was cluttering the tree)
apps/accounts/models_snippet.py, views.py.patch, config/urls.py.new/.patch,
dashboard.html.new, root services/ + tasks/ dirs, tree.txt, file_tree.txt,
output.txt, empty `git` file. Binaries ngrok.exe + GeoLite2-Country.mmdb are
kept on disk in your repo but excluded here — add them to .gitignore instead
of committing.

## Left unchanged (deliberately)
services/telegram.py + services/discord.py (already clean API clients),
BotAccessAudit schema, subscriptions/api.py gift engine, the DM-based invite
mechanism, the 11-app layout, all other apps.
