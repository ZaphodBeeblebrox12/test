# Generated manually — ReferralSettings + Referral only (ReferralReward unchanged)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("growth", "0009_remove_referral_growth_refer_fraud_check_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="referralsettings",
            name="referee_benefit_enabled",
            field=models.BooleanField(
                default=False,
                help_text="If True, referred user receives a one-time credit bonus on qualifying purchase",
            ),
        ),
        migrations.AddField(
            model_name="referralsettings",
            name="referee_bonus_cents",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Fixed credit (cents) granted to the referred user once per referral",
            ),
        ),
        migrations.AddField(
            model_name="referral",
            name="referee_reward_applied",
            field=models.BooleanField(
                default=False,
                help_text="True once the one-time referee bonus credit has been granted",
            ),
        ),
    ]
