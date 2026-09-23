"""Admin index helpers: capability grouping + automation health strip."""
from django import template
from django.utils import timezone

register = template.Library()

# (title, {(app_label, model_name_lower), ...}) - order defines display order.
GROUPS = [
    ("💰 Billing & Money", {
        ("payments", "paymentintent"), ("payments", "refund"),
        ("payments", "webhookevent"),
        ("subscriptions", "subscription"), ("subscriptions", "subscriptionhistory"),
        ("subscriptions", "upgradehistory"), ("subscriptions", "plan"),
        ("subscriptions", "planprice"), ("subscriptions", "geoplanprice"),
        ("promotions", "coupon"), ("promotions", "couponredemption"),
    }),
    ("🛡 Access & Community", {
        ("bot_integration", "telegramaccount"), ("bot_integration", "discordaccount"),
        ("bot_integration", "communitychannel"),
        ("bot_integration", "channelmembershipsnapshot"),
        ("bot_integration", "planchannelmapping"),
        ("bot_integration", "userchannelassignment"),
        ("bot_integration", "botaccessaudit"), ("bot_integration", "botconfig"),
        ("bot_integration", "telegramverificationtoken"),
    }),
    ("🚀 Growth & Lifecycle", {
        ("growth", "referral"), ("growth", "referralcode"),
        ("growth", "referralreward"), ("growth", "referralsettings"),
        ("growth", "marketingpreference"), ("growth", "giftinvite"),
        ("growth", "suppressionlist"),
        ("campaigns", "campaign"), ("campaigns", "audience"),
        ("campaigns", "campaignrecipient"), ("campaigns", "campaignmetrics"),
        ("automation", "automationrule"), ("events", "event"),
    }),
    ("🔔 Messaging", {
        ("notifications", "notification"), ("notifications", "emaillog"),
    }),
    ("⚙ System & Operations", {
        ("jobs", "job"), ("jobs", "periodicjob"),
        ("audit", "auditlog"),
        ("auth", "user"), ("auth", "group"),
        ("sites", "site"), ("system_settings", "systemsettings"),
    }),
    ("📧 Email Builder (future)", {
        ("emailing", "template"), ("emailing", "templateversion"),
        ("emailing", "mediaasset"), ("emailing", "delivery"),
        ("emailing", "contentblock"), ("emailing", "variablespec"),
    }),
]

OTHER = "📦 Other"


@register.simple_tag(takes_context=True)
def grouped_app_list(context):
    """Bucket the (permission-filtered) app_list by business capability."""
    app_list = context.get("app_list") or []
    buckets = {title: [] for title, _ in GROUPS}
    buckets[OTHER] = []
    for app in app_list:
        for model in app.get("models", []):
            key = (app.get("app_label"), model.get("object_name", "").lower())
            for title, members in GROUPS:
                if key in members:
                    buckets[title].append(model)
                    break
            else:
                buckets[OTHER].append(model)
    result = [{"title": t, "models": buckets[t]} for t, _ in GROUPS if buckets[t]]
    if buckets[OTHER]:
        result.append({"title": OTHER, "models": buckets[OTHER]})
    return result


@register.simple_tag
def admin_health():
    """Morning-check strip: pending jobs + periodic job last-runs."""
    from apps.jobs.models import Job, PeriodicJob
    pending = Job.objects.filter(status="pending").count()
    periodic = [
        {"name": j.name, "enabled": j.enabled, "last_status": j.last_status,
         "last_run_at": j.last_run_at}
        for j in PeriodicJob.objects.all().order_by("name")
    ]
    return {"pending": pending, "periodic": periodic, "now": timezone.now()}
