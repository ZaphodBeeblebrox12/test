import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0010_subscriptionreminder_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlanFeature",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("text", models.CharField(max_length=255)),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("plan", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="features",
                    to="subscriptions.plan")),
            ],
            options={
                "verbose_name": "plan feature",
                "verbose_name_plural": "plan features",
                "ordering": ["position", "id"],
            },
        ),
    ]
