import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds PolicyAcceptance.checkout_evidence (nullable FK).

    The rewritten 0001_initial dropped this field that the model declares;
    heals the drift without touching 0001.
    """
    dependencies = [
        ("policies", "0002_seed_placeholder_versions"),
        ("evidence", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="policyacceptance",
            name="checkout_evidence",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="policy_acceptances",
                to="evidence.checkoutevidence"),
        ),
    ]
