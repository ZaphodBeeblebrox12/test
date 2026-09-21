from django.db import migrations

CHANNELS = (
    ("email", "Email"),
    ("telegram", "Telegram marketing"),
    ("in_product", "In-product announcement"),
)


def seed_channels(apps, schema_editor):
    Channel = apps.get_model("advanced_growth", "Channel")
    for kind, name in CHANNELS:
        Channel.objects.get_or_create(kind=kind, defaults={"name": name})


def unseed_channels(apps, schema_editor):
    Channel = apps.get_model("advanced_growth", "Channel")
    Channel.objects.filter(kind__in=[k for k, _ in CHANNELS]).delete()


class Migration(migrations.Migration):
    dependencies = [("advanced_growth", "0001_initial")]
    operations = [migrations.RunPython(seed_channels, unseed_channels)]
