import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("subscriptions", "0011_planfeature"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="is_hidden",
            field=models.BooleanField(default=False, help_text="Hidden plans never appear on the landing page, dashboard, or purchase API. Access is granted by admin/ticket approval only."),
        ),
        migrations.AddField(
            model_name="plan",
            name="grant_duration_days",
            field=models.PositiveIntegerField(blank=True, null=True, help_text="For approval-granted (hidden) plans: subscription length in days after approval. Leave empty for no expiry."),
        ),
        migrations.AddField(
            model_name="plan",
            name="notice_channel",
            field=models.CharField(choices=[("telegram", "Telegram"), ("email", "Email"), ("both", "Both")], default="telegram", help_text="Where approval/rejection notices are sent for access-request tickets on this plan", max_length=10),
        ),
        migrations.AddField(
            model_name="plan",
            name="ticket_approvers",
            field=models.ManyToManyField(blank=True, help_text="Staff allowed to approve/reject access tickets for this plan. Empty = any staff. Superusers can always approve.", limit_choices_to={"is_staff": True}, related_name="approvable_plans", to=settings.AUTH_USER_MODEL),
        ),
    ]
