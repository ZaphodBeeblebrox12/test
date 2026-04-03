# Generated safety patch migration
# Apply with: python manage.py migrate

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Add safety fields to prevent referral fraud:
    - discount_used: One-time discount enforcement
    - reward_created: Single reward per referral tracking
    """

    dependencies = [
        ('growth', '0001_initial'),  # Adjust to your last migration
    ]

    operations = [
        # Add discount_used to Referral model
        migrations.AddField(
            model_name='referral',
            name='discount_used',
            field=models.BooleanField(
                default=False,
                help_text='Has the referee used their one-time discount?'
            ),
        ),

        # Add reward_created flag to Referral model
        migrations.AddField(
            model_name='referral',
            name='reward_created',
            field=models.BooleanField(
                default=False,
                help_text='Has reward been created for this referral?'
            ),
        ),

        # Add index for faster fraud checks
        migrations.AddIndex(
            model_name='referral',
            index=models.Index(
                fields=['referrer', 'referred_user', 'status'],
                name='growth_refer_fraud_check_idx'
            ),
        ),
    ]
