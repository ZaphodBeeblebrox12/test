"""Apply the 3 product-scoping edits (patches/SCOPE_EDITS.md) automatically.

Run from the project root (venv active):
    python apply_scope_edits.py

Safety: for each edit, the FIND block must appear EXACTLY ONCE in the
target file, otherwise the file is left untouched and the script exits 1.
A timestamped .bak backup is made for every file it modifies.
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()  # run from the project root

EDITS = [
    {
        "file": "apps/subscriptions/services.py",
        "find": """    # Deactivate any existing active subscriptions
    Subscription.objects.filter(user=user, is_active=True).update(
        is_active=False, status=Subscription.Status.CANCELED, canceled_at=timezone.now()
    )""",
        "replace": """    # Deactivate existing active subscriptions IN THE SAME PRODUCT only;
    # subscriptions in other products are independent entitlements.
    # product=None keeps the legacy GLOBAL behavior.
    same_product = Subscription.objects.filter(user=user, is_active=True)
    if plan.product_id is not None:
        same_product = same_product.filter(product_id=plan.product_id)
    same_product.update(
        is_active=False, status=Subscription.Status.CANCELED, canceled_at=timezone.now()
    )""",
    },
    {
        "file": "apps/payments/services.py",
        "find": """    subscription = (Subscription.objects
                    .filter(user=user, status=Subscription.Status.ACTIVE,
                            is_active=True)
                    .select_related("plan", "plan_price").first())""",
        "replace": """    subscription = (Subscription.objects
                    .filter(user=user, status=Subscription.Status.ACTIVE,
                            is_active=True)
                    .select_related("plan", "plan_price"))
    if target_plan.product_id is not None:
        subscription = subscription.filter(product_id=target_plan.product_id)
    subscription = subscription.first()""",
    },
    {
        "file": "apps/payments/views.py",
        "find": """        candidates = (Plan.objects
                      .filter(is_active=True, is_trial=False,
                              display_order__gt=subscription.plan.display_order)
                      .order_by("display_order"))""",
        "replace": """        candidates = (Plan.objects
                      .filter(is_active=True, is_trial=False,
                              display_order__gt=subscription.plan.display_order)
                      .order_by("display_order"))
        if subscription.plan.product_id is not None:
            candidates = candidates.filter(product_id=subscription.plan.product_id)
        else:
            candidates = candidates.filter(product__isnull=True)""",
    },
]


ADMIN_FILE = "apps/subscriptions/admin.py"
ADMIN_LINE = "from . import admin_product_extension  # noqa: F401"


def patch_admin():
    path = ROOT / ADMIN_FILE
    if not path.exists():
        print(f"MISSING FILE : {ADMIN_FILE} -- skipped")
        return 1
    src = path.read_text(encoding="utf-8")
    if ADMIN_LINE in src:
        print(f"ALREADY DONE : {ADMIN_FILE}")
        return 0
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    path.write_text(src.rstrip("\n") + "\n\n" + ADMIN_LINE + "\n",
                    encoding="utf-8")
    print(f"APPLIED       : {ADMIN_FILE}  (backup: {backup.name})")
    return 0


def main():
    print(f"Looking for files under: {ROOT}\n")
    failures = 0
    for edit in EDITS:
        path = ROOT / edit["file"]
        if not path.exists():
            print(f"MISSING FILE : {edit['file']} -- skipped")
            failures += 1
            continue
        src = path.read_text(encoding="utf-8")
        if edit["replace"] in src:
            print(f"ALREADY DONE : {edit['file']}")
            continue
        n = src.count(edit["find"])
        if n != 1:
            print(f"NO MATCH ({n}x): {edit['file']} -- left UNTOUCHED. "
                  f"File differs from expected; paste it for a manual patch.")
            failures += 1
            continue
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        path.write_text(src.replace(edit["find"], edit["replace"]), encoding="utf-8")
        print(f"APPLIED       : {edit['file']}  (backup: {backup.name})")

    failures += patch_admin()
    if failures:
        print(f"\n{failures} edit(s) NOT applied -- see above.")
        sys.exit(1)
    print("\nAll edits applied. Now run: python manage.py check && python manage.py test")


if __name__ == "__main__":
    main()
