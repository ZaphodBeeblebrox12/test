"""Risk-analyst group bootstrap (must run AFTER risk permissions exist -
that is why it lives here and not in the disputes app)."""
from django.db import migrations


def bootstrap(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    risk, _ = Group.objects.get_or_create(name="risk_analyst")
    risk.permissions.set(list(Permission.objects.filter(codename__in=[
        "view_risksignal", "change_risksignal",
        "view_checkoutevidence", "view_paymentauthentication",
        "view_securityevent",
    ])))


def teardown(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="risk_analyst").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("risk", "0001_initial"),
        ("evidence", "0001_initial"),
    ]
    operations = [migrations.RunPython(bootstrap, teardown)]
