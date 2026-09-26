import uuid

import apps.support.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("subscriptions", "0012_plan_hidden_ticket_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="AffiliateLink",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(help_text='Label shown to users, e.g. "Exness" or "IC Markets VIP"', max_length=100)),
                ("url", models.URLField(help_text="Your referral link for this broker/code")),
                ("referral_code", models.CharField(help_text="The code the broker gave you - used to verify screenshots", max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("plan", models.ForeignKey(blank=True, help_text="Optional: hidden plan auto-assigned when an access request using this link is approved (you can still override)", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="affiliate_links", to="subscriptions.plan")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="SupportTicket",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("category", models.CharField(choices=[("access_request", "Access request (referral verification)"), ("billing", "Billing / payment issue"), ("technical", "Technical issue (Telegram access, etc.)")], default="access_request", max_length=20)),
                ("message", models.TextField(help_text="Describe the request or issue")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved / Resolved"), ("rejected", "Rejected / Closed")], default="pending", max_length=10)),
                ("admin_notes", models.TextField(blank=True, default="", help_text="Shown to the user on rejection (and stored for approved tickets)")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("affiliate_link", models.ForeignKey(blank=True, help_text="Which referral link was used (access requests only)", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets", to="support.affiliatelink")),
                ("plan", models.ForeignKey(blank=True, help_text="Assigned by the approver at approval time (access requests)", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_tickets", to="subscriptions.plan")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_support_tickets", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(help_text="User who raised the ticket", on_delete=django.db.models.deletion.CASCADE, related_name="support_tickets", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "support ticket",
                "verbose_name_plural": "support tickets",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="TicketAttachment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("file", models.FileField(upload_to=apps.support.models.ticket_attachment_upload_path)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="support.supportticket")),
            ],
            options={
                "verbose_name": "ticket attachment",
                "verbose_name_plural": "ticket attachments",
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="supportticket",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "pending")), fields=("user",), name="uniq_one_pending_ticket_per_user"),
        ),
    ]
