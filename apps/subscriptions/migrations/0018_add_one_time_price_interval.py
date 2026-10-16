from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0017_strip_legacy_plan_unique")]
    operations = [
        migrations.AlterField(
            model_name="planprice", name="interval",
            field=models.CharField(choices=[("monthly", _("Monthly")), ("quarterly", _("Quarterly")), ("yearly", _("Yearly")), ("one_time", _("One-time purchase"))], max_length=10),
        ),
        migrations.AlterField(
            model_name="geoplanprice", name="interval",
            field=models.CharField(choices=[("monthly", _("Monthly")), ("quarterly", _("Quarterly")), ("yearly", _("Yearly")), ("one_time", _("One-time purchase"))], max_length=10),
        ),
    ]
