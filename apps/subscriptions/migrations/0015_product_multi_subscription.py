"""
Product-aware subscriptions.

Adds the Product model and nullable product FKs on Plan and Subscription.
Mutual exclusion is now scoped to the product (Subscription.save) — a user
may hold one active subscription PER PRODUCT. Plans with product=NULL keep
the legacy GLOBAL behavior exactly (one active subscription across all
ungrouped plans), so existing data and tests are unaffected.

Replaces Plan.unique_together = ["tier", "is_trial"] with two conditional
unique constraints: one per (tier, is_trial) among ungrouped plans, and one
per (tier, is_trial) within each product.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0014_alter_planfeature_plan_alter_planfeature_position_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(help_text="Display name for the product (e.g. 'Trade Thesis')", max_length=100)),
                ("slug", models.SlugField(help_text="URL/admin identifier (e.g. 'trade-thesis')", max_length=50, unique=True)),
                ("description", models.TextField(blank=True, help_text="Short description shown on the dashboard explore section")),
                ("is_active", models.BooleanField(default=True, help_text="Whether this product is offered to users")),
                ("display_order", models.PositiveSmallIntegerField(default=0, help_text="Order in product lists (lower = shown first)")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "product",
                "verbose_name_plural": "products",
                "ordering": ["display_order", "name"],
            },
        ),
        migrations.AddField(
            model_name="plan",
            name="product",
            field=models.ForeignKey(
                blank=True,
                help_text="Product this plan belongs to. Empty = ungrouped (legacy global hierarchy): one active subscription across ALL ungrouped plans per user.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="plans",
                to="subscriptions.product",
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="product",
            field=models.ForeignKey(
                blank=True,
                help_text="Product of the subscribed plan (snapshot at creation).",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subscriptions",
                to="subscriptions.product",
            ),
        ),
        # Old global uniqueness is replaced by the two conditional
        # constraints below (must be removed first).
        migrations.AlterUniqueTogether(
            name="plan",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="plan",
            constraint=models.UniqueConstraint(
                condition=models.Q(("product__isnull", True)),
                fields=("tier", "is_trial"),
                name="unique_ungrouped_tier_trial",
                violation_error_message="Only one plan per tier is allowed outside a product (ungrouped).",
            ),
        ),
        migrations.AddConstraint(
            model_name="plan",
            constraint=models.UniqueConstraint(
                condition=models.Q(("product__isnull", False)),
                fields=("product", "tier", "is_trial"),
                name="unique_product_tier_trial",
                violation_error_message="A plan with this tier already exists in this product.",
            ),
        ),
    ]
