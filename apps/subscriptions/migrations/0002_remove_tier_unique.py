# Generated migration to remove unique constraint from Plan.tier
# This allows multiple plans per tier (regular + trial)

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('subscriptions', '0001_initial'),  # Update this to your last migration
    ]

    operations = [
        # Remove the unique constraint from tier field
        migrations.AlterField(
            model_name='plan',
            name='tier',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('free', 'Free'),
                    ('basic', 'Basic'),
                    ('pro', 'Pro'),
                    ('enterprise', 'Enterprise'),
                ],
                help_text='Plan tier level'
                # Note: unique=True is removed!
            ),
        ),
    ]
