"""Community channel registry + observed membership snapshots (DISPLAY ONLY).

Snapshots record what Telegram reports via getChatMember; they never drive
access control (entitlements remain UserChannelAssignment/reconcile).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bot_integration", "0006_telegram_account_profile"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CommunityChannel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                     serialize=False, verbose_name="ID")),
                ("platform", models.CharField(choices=[("telegram", "Telegram"),
                     ("discord", "Discord")], default="telegram", max_length=10)),
                ("external_id", models.CharField(help_text=
                     "Telegram chat id or @username", max_length=255)),
                ("name", models.CharField(blank=True, max_length=255)),
                ("invite_url", models.URLField(blank=True, help_text=
                     "Optional public invite for non-members")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "Community Channel",
                     "verbose_name_plural": "Community Channels"},
        ),
        migrations.CreateModel(
            name="ChannelMembershipSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                     serialize=False, verbose_name="ID")),
                ("is_member", models.BooleanField(default=False)),
                ("checked_at", models.DateTimeField()),
                ("channel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                     related_name="membership_snapshots",
                     to="bot_integration.communitychannel")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                     related_name="channel_membership_snapshots",
                     to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Channel Membership Snapshot",
                     "verbose_name_plural": "Channel Membership Snapshots"},
        ),
        migrations.AlterUniqueTogether(
            name="communitychannel",
            unique_together={("platform", "external_id")},
        ),
        migrations.AlterUniqueTogether(
            name="channelmembershipsnapshot",
            unique_together={("user", "channel")},
        ),
    ]
