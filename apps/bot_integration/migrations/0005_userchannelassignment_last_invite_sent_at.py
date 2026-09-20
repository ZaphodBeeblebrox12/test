from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_integration', '0004_remove_botconfig_telegram_control_channel_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='userchannelassignment',
            name='last_invite_sent_at',
            field=models.DateTimeField(
                blank=True,
                default=None,
                help_text='Last time an invite was actually (re)sent for this '
                          'assignment; drives the persistent 6h resend throttle.',
                null=True,
            ),
        ),
    ]
