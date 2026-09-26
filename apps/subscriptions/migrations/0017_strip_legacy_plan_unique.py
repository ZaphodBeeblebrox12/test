"""
Strip the legacy in-table UNIQUE(tier, is_trial) from subscriptions_plan.

Root cause of the surviving constraint: Django implements unique_together
on SQLite as an in-table UNIQUE clause (surfaced as sqlite_autoindex_*),
NOT a standalone index, and this database's migration state never tracked
Plan.unique_together (local history divergence), so 0015's
AlterUniqueTogether no-op'd and no table rebuild occurred. 0016 only
removed standalone indexes and could not touch this one.

This migration rebuilds the table with the clause removed, preserving all
data, foreign-key references, and re-creating the conditional unique
indexes and the product FK index. SQLite-only; no-op elsewhere. Safe to
re-run (returns early when the clause is absent).
"""
import re

from django.db import migrations

_LEGACY_UNIQUE_RX = re.compile(
    r",\s*UNIQUE\s*\(\s*[`\"']?tier[`\"']?\s*,\s*[`\"']?is_trial[`\"']?\s*\)",
    re.IGNORECASE,
)


def _rebuild_without_legacy_unique(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != "sqlite":
        return
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'subscriptions_plan'"
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return
    ddl = row[0]
    new_ddl = _LEGACY_UNIQUE_RX.sub("", ddl)
    if new_ddl == ddl:
        return  # clause already gone

    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.execute("BEGIN")
    try:
        cursor.execute(new_ddl.replace(
            '"subscriptions_plan"', '"subscriptions_plan__new"', 1))
        cols = [r[1] for r in cursor.execute(
            "PRAGMA table_info('subscriptions_plan')")]
        col_list = ", ".join('"%s"' % c for c in cols)
        cursor.execute(
            'INSERT INTO "subscriptions_plan__new" (%s) '
            'SELECT %s FROM "subscriptions_plan"' % (col_list, col_list))
        cursor.execute('DROP TABLE "subscriptions_plan"')
        cursor.execute(
            'ALTER TABLE "subscriptions_plan__new" RENAME TO "subscriptions_plan"')
        cursor.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS "unique_ungrouped_tier_trial" '
            'ON "subscriptions_plan" (tier, is_trial) WHERE product IS NULL')
        cursor.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS "unique_product_tier_trial" '
            'ON "subscriptions_plan" (product, tier, is_trial) '
            'WHERE product IS NOT NULL')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS "subscriptions_plan_product_id_8a2614e5" '
            'ON "subscriptions_plan" (product_id)')
        cursor.execute("COMMIT")
    except Exception:
        cursor.execute("ROLLBACK")
        raise
    finally:
        cursor.execute("PRAGMA foreign_keys = ON")


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False  # explicit transaction + PRAGMA in the operation

    dependencies = [
        ("subscriptions", "0016_drop_legacy_plan_unique"),
    ]

    operations = [
        migrations.RunPython(_rebuild_without_legacy_unique, _noop),
    ]
