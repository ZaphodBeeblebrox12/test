import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0004_paymentintent_provider_reference"),
    ]

    operations = [
        migrations.CreateModel(
            name="WebhookEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider", models.CharField(choices=[("stripe", "Stripe"), ("razorpay", "Razorpay")], max_length=20)),
                ("provider_event_id", models.CharField(max_length=255)),
                ("event_type", models.CharField(max_length=100)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("received", "Received"), ("processed", "Processed"), ("failed", "Failed"), ("ignored", "Ignored")], default="received", max_length=20)),
                ("error", models.TextField(blank=True, default="")),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.AddConstraint(
            model_name="webhookevent",
            constraint=models.UniqueConstraint(fields=("provider", "provider_event_id"), name="uniq_provider_event"),
        ),
        migrations.AddIndex(
            model_name="webhookevent",
            index=models.Index(fields=["status", "provider"], name="pay_wh_status_idx"),
        ),
    ]
