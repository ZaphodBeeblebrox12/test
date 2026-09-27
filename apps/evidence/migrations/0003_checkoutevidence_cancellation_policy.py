import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds CheckoutEvidence.cancellation_policy_version (nullable FK).

    0001_initial shipped without this column while the capture service
    already referenced it; heals the drift without touching 0001.
    """
    dependencies = [
        ("evidence", "0002_plan_refund_terms"),
        ("policies", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkoutevidence",
            name="cancellation_policy_version",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="checkout_cancellation_evidence",
                to="policies.policyversion"),
        ),
    ]
