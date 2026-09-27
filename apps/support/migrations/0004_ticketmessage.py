import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0003_alter_affiliatelink_plan"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TicketMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("body", models.TextField()),
                ("is_internal", models.BooleanField(
                    default=False,
                    help_text="Staff-only note; never shown to the user.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(
                    blank=True, help_text="Null = deleted account; body retained, "
                    "author shown as 'former user'.", null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
                ("ticket", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="messages", to="support.supportticket")),
            ],
            options={
                "verbose_name": "ticket message",
                "verbose_name_plural": "ticket messages",
                "ordering": ["created_at", "id"],
                "indexes": [models.Index(fields=["ticket", "created_at"],
                                         name="support_tm_ticket_ts")],
            },
        ),
    ]
