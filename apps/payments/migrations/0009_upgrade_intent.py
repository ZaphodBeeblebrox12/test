"""PaymentIntent.is_upgrade: marks prorated upgrade purchases."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("payments", "0008_refunds_and_chargebacks")]

    operations = [
        migrations.AddField(
            model_name="paymentintent",
            name="is_upgrade",
            field=models.BooleanField(default=False,
                help_text="Prorated upgrade purchase; on activation writes UpgradeHistory + upgraded history."),
        ),
    ]
