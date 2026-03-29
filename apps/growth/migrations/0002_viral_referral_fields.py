# Generated migration for Phase 4 Viral Referral System
# Adds delayed reward unlock, explicit subscription link, and refund handling fields

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('growth', '0001_initial'),  # Adjust this to your last migration
        ('subscriptions', '0001_initial'),  # Ensure subscriptions is loaded first
    ]

    operations = [
        # Add reward_delay_hours to ReferralSettings
        migrations.AddField(
            model_name='referralsettings',
            name='reward_delay_hours',
            field=models.PositiveIntegerField(
                default=72,
                help_text='Hours to delay before unlocking referral rewards (default: 72 = 3 days)'
            ),
        ),

        # Add unlocked_at to ReferralReward
        migrations.AddField(
            model_name='referralreward',
            name='unlocked_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When this reward becomes available (after delay)',
                null=True
            ),
        ),

        # Add block_reason to ReferralReward
        migrations.AddField(
            model_name='referralreward',
            name='block_reason',
            field=models.CharField(
                blank=True,
                help_text='Reason if reward was blocked (circular, refunded, etc.)',
                max_length=50
            ),
        ),

        # Add triggering_subscription FK to ReferralReward
        migrations.AddField(
            model_name='referralreward',
            name='triggering_subscription',
            field=models.ForeignKey(
                blank=True,
                help_text='The subscription that triggered this reward (for refund checking)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='referral_rewards',
                to='subscriptions.subscription'
            ),
        ),

        # Add index for efficient unlock queries
        migrations.AddIndex(
            model_name='referralreward',
            index=models.Index(
                fields=['status', 'unlocked_at'],
                name='growth_refer_unlock_idx'
            ),
        ),
    ]
