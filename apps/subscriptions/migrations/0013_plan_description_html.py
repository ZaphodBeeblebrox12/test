from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0012_plan_hidden_ticket_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="description_html",
            field=models.TextField(blank=True, default="", help_text="Optional rich HTML shown on the landing page and dashboard card above the feature bullets. Sanitized on save: safe tags only (p, br, ul, ol, li, b, strong, i, em, u, s, a, blockquote, code, h4-h6); scripts and styling are stripped."),
        ),
    ]
