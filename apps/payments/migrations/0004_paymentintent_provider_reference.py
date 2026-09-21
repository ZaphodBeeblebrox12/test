from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_paymentintent_applied_referral_discount"),
        ("subscriptions", "0008_subscription_geo_plan_price_subscription_price_cents_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentintent",
            name="geo_plan_price",
            field=models.ForeignKey(
                blank=True,
                help_text="Snapshot of the regional price used for this payment (null when a global PlanPrice applied).",
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="payment_intents",
                to="subscriptions.geoplanprice",
            ),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="provider_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Provider checkout/order/session id (Stripe Checkout Session id or Razorpay Order id).",
                max_length=255,
            ),
        ),
    ]
