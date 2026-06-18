from django.conf import settings
from django.db import migrations


def seed_plans(apps, schema_editor):
    if apps.get_model("billing", "SubscriptionPlan").objects.exists():
        return

    currency = getattr(settings, "BILLING_CURRENCY", "USD")
    from billing.plan_seed import sync_subscription_plans

    sync_subscription_plans(currency=currency)


def unseed_plans(apps, schema_editor):
    SubscriptionPlan = apps.get_model("billing", "SubscriptionPlan")
    SubscriptionPlan.objects.filter(slug__in=["starter", "growth", "enterprise"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_tiers_and_limits"),
    ]

    operations = [
        migrations.RunPython(seed_plans, unseed_plans),
    ]
