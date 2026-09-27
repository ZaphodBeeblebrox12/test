from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("disputes", "0002_bootstrap_groups")]

    operations = [
        migrations.AddField(
            model_name="disputeevidencepackage",
            name="submission_reference",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
