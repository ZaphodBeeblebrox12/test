import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0009_upgrade_intent")]

    operations = [
        migrations.AddField(
            model_name="refund",
            name="commercial_context",
            field=models.CharField(choices=[
                ("trial", "Trial payment (incl. conversion charge)"),
                ("standard", "Standard paid plan"),
                ("upgrade", "Upgrade purchase")],
                default="standard", max_length=20),
        ),
        migrations.AddIndex(
            model_name="refund",
            index=models.Index(fields=["commercial_context", "refunded_at"],
                               name="pay_refund_ctx_ts"),
        ),
    ]
