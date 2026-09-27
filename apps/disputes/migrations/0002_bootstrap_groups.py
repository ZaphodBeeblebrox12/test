"""Create dispute/risk permission groups (idempotent)."""
from django.db import migrations


def bootstrap(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    def perms(codenames):
        return list(Permission.objects.filter(codename__in=codenames))

    staff, _ = Group.objects.get_or_create(name="disputes_staff")
    staff.permissions.set(perms([
        "view_dispute", "change_dispute", "view_disputeevent",
        "view_disputeevidencepackage", "change_disputeevidencepackage",
        "view_checkoutevidence", "view_paymentauthentication",
        "view_cancellationrequest", "view_membershipconfirmation",
        "view_securityevent", "view_policyversion", "view_policyacceptance",
        "view_ticketmessage",
    ]))
    lead, _ = Group.objects.get_or_create(name="disputes_lead")
    lead.permissions.set(perms([
        "view_dispute", "change_dispute", "delete_dispute",
        "view_disputeevent", "view_disputeevidencepackage",
        "change_disputeevidencepackage",
        "view_checkoutevidence", "view_paymentauthentication",
        "view_cancellationrequest", "view_membershipconfirmation",
        "view_securityevent", "view_policyversion", "view_policyacceptance",
        "view_ticketmessage",
    ]))


def teardown(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=["disputes_staff", "disputes_lead"]).delete()


class Migration(migrations.Migration):
    dependencies = [("disputes", "0001_initial")]
    operations = [migrations.RunPython(bootstrap, teardown)]
