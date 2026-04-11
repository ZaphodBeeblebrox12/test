# Generated Django migration for Trial Plan Support

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    """
    Add trial plan support to subscriptions app.
    """

    dependencies = [
        ('accounts', '0001_initial'),
        ('subscriptions', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='is_trial',
            field=models.BooleanField(
                default=False,
                help_text='Whether this is a one-time trial plan'
            ),
        ),
        migrations.AddField(
            model_name='plan',
            name='trial_duration_days',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text='Duration of trial in days (required if is_trial=True)'
            ),
        ),
        migrations.AddField(
            model_name='subscription',
            name='is_trial',
            field=models.BooleanField(
                default=False,
                help_text='Whether this subscription is a trial'
            ),
        ),
        migrations.CreateModel(
            name='UserTrialUsage',
            fields=[
                ('id', models.UUIDField(
                    default=uuid.uuid4, 
                    editable=False, 
                    primary_key=True, 
                    serialize=False
                )),
                ('used_at', models.DateTimeField(
                    auto_now_add=True,
                    help_text='When the trial was claimed'
                )),
                ('expires_at', models.DateTimeField(
                    help_text='When the trial expires'
                )),
                ('plan', models.ForeignKey(
                    help_text='Trial plan that was used',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='trial_usages',
                    to='subscriptions.plan'
                )),
                ('subscription', models.ForeignKey(
                    blank=True,
                    help_text='Subscription created from this trial',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='trial_usage_record',
                    to='subscriptions.subscription'
                )),
                ('user', models.ForeignKey(
                    help_text='User who used the trial',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='trial_usages',
                    to=settings.AUTH_USER_MODEL
                )),
            ],
            options={
                'verbose_name': 'user trial usage',
                'verbose_name_plural': 'user trial usages',
                'ordering': ['-used_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='usertrialusage',
            constraint=models.UniqueConstraint(
                fields=('user', 'plan'),
                name='unique_user_trial_per_plan'
            ),
        ),
    ]
