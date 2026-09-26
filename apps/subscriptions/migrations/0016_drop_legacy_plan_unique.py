"""
Drop the legacy global UNIQUE index on (tier, is_trial) left behind on
SQLite databases whose applied migration state never tracked
Plan.unique_together (state/DB divergence from pre-product migration
history). The new conditional constraints (unique_ungrouped_tier_trial,
unique_product_tier_trial) are untouched — they are identified by the
presence of the product column in their index SQL, and the legacy index by
its absence.

Safe to run on databases that no longer have the legacy index (IF EXISTS +
pattern guard). No-op on non-SQLite vendors: Postgres deployments created
after 0015 never had the index.
"""
from django.db import migrations


def _drop_legacy_plan_unique(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'subscriptions_plan'"
        )
        names = [row[0] for row in cursor.fetchall()]
        for name in names:
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = %s",
                [name],
            )
            row = cursor.fetchone()
            sql = (row[0] or "") if row else ""
            # Legacy index: covers tier + is_trial, WITHOUT the product
            # column. The new conditional constraints reference product
            # (IS NULL / IS NOT NULL) and are skipped.
            if ("tier" in sql and "is_trial" in sql
                    and "product" not in sql.lower()):
                cursor.execute('DROP INDEX IF EXISTS "%s"' % name)


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0015_product_multi_subscription"),
    ]

    operations = [
        migrations.RunPython(_drop_legacy_plan_unique, _noop),
    ]
