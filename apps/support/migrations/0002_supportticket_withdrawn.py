from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supportticket",
            name="status",
            field=models.CharField(choices=[("pending", "Pending"), ("approved", "Approved / Resolved"), ("rejected", "Rejected / Closed"), ("withdrawn", "Withdrawn by user")], default="pending", max_length=10),
        ),
    ]
