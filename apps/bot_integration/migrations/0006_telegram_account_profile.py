"""TelegramAccount profile enrichment: username, first_name, cached avatar."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bot_integration", "0005_userchannelassignment_last_invite_sent_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramaccount",
            name="username",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="telegramaccount",
            name="first_name",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="telegramaccount",
            name="avatar",
            field=models.ImageField(blank=True, null=True, upload_to="telegram_avatars/"),
        ),
    ]
