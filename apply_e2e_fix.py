#!/usr/bin/env python
"""One-shot fix: CustomerLifecycleE2E.run_webhook_job helper.

Bug: WebhookEvent PKs are UUIDs, so .latest("id") orders by RANDOM uuid,
not creation time - the helper can pick an already-processed older event
(intermittent failure at base, independent of any billing work).

Fix: select by received_at (creation) instead. Idempotent; anchored.
Run from project root:   python apply_e2e_fix.py
"""
import sys

path = "apps/subscriptions/tests/test_customer_lifecycle_e2e.py"
try:
    src = open(path, encoding="utf-8").read()
except FileNotFoundError:
    print(f"[FAIL] {path}: not found (run from project root)")
    sys.exit(1)

if 'order_by("-received_at")' in src:
    print(f"[SKIP] {path}: already fixed")
    sys.exit(0)

old = "        ev = WebhookEvent.objects.latest(\"id\")"
new = ('        # UUID PKs: latest("id") orders by random uuid, not time -\n'
       '        # pick the actually-newest event by receive time.\n'
       '        ev = WebhookEvent.objects.order_by(\"-received_at\").first()')
n = src.count(old)
if n != 1:
    print(f"[FAIL] {path}: anchor matched {n} times - UNCHANGED")
    sys.exit(1)
src = src.replace(old, new)
compile(src, path, "exec")
open(path, "w", encoding="utf-8").write(src)
print(f"[OK]   {path}: helper now selects newest event by received_at")
